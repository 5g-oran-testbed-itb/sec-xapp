"""
Anomaly Detector - Kombinasi LSTM dan Threshold Rules
Bagian dari Security xApp Prototype
"""

import numpy as np
import torch
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

from ..virtual_ue.e2_agent import KPIMetrics
from .feature_schema import FEATURE_NAMES, NUM_FEATURES

# Index mapping dari FEATURE_NAMES untuk akses cepat
# ['dl_throughput_mbps', 'ul_throughput_mbps', 'uplink_downlink_ratio',
#  'throughput_gradient', 'snr_db', 'cqi', 'prb_usage_dl_ratio',
#  'prb_usage_ul_ratio', 'rrc_attempt_rate', 'rrc_success_rate',
#  'rrc_failure_rate', 'burst_index']
_IDX_DL_MBPS      = FEATURE_NAMES.index('dl_throughput_mbps')
_IDX_UL_MBPS      = FEATURE_NAMES.index('ul_throughput_mbps')
_IDX_UL_DL_RATIO  = FEATURE_NAMES.index('uplink_downlink_ratio')
_IDX_GRADIENT     = FEATURE_NAMES.index('throughput_gradient')
_IDX_SNR          = FEATURE_NAMES.index('snr_db')
_IDX_CQI          = FEATURE_NAMES.index('cqi')
_IDX_PRB_DL       = FEATURE_NAMES.index('prb_usage_dl_ratio')
_IDX_PRB_UL       = FEATURE_NAMES.index('prb_usage_ul_ratio')
_IDX_RRC_ATT      = FEATURE_NAMES.index('rrc_attempt_rate')
_IDX_RRC_SUC      = FEATURE_NAMES.index('rrc_success_rate')
_IDX_RRC_FAIL     = FEATURE_NAMES.index('rrc_failure_rate')
_IDX_BURST        = FEATURE_NAMES.index('burst_index')


class AnomalyType(Enum):
    """Jenis anomali yang dapat dideteksi"""
    NORMAL = "normal"
    PRB_OVERLOAD = "prb_overload"
    SIGNALING_STORM = "signaling_storm"
    JAMMING = "jamming"
    THROUGHPUT_ANOMALY = "throughput_anomaly"
    UPLINK_FLOOD = "uplink_flood"
    DOWNLINK_FLOOD = "downlink_flood"
    UE_ANOMALY = "ue_anomaly"
    UNKNOWN = "unknown"


class Severity(Enum):
    """Tingkat keparahan anomali"""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class AnomalyResult:
    """Hasil deteksi anomali"""
    timestamp: float
    is_anomaly: bool
    anomaly_type: AnomalyType
    severity: Severity
    confidence: float  # 0.0 - 1.0
    details: Dict
    kpi_snapshot: Dict
    
    def to_dict(self) -> Dict:
        # Convert any numpy types to Python native types for JSON serialization
        def convert_value(v):
            if hasattr(v, 'item'):  # numpy scalar
                return v.item()
            return v
        
        details = {k: convert_value(v) for k, v in self.details.items()}
        
        return {
            'timestamp': float(self.timestamp),
            'datetime': datetime.fromtimestamp(self.timestamp).isoformat(),
            'is_anomaly': self.is_anomaly,
            'anomaly_type': self.anomaly_type.value,
            'severity': self.severity.name,
            'confidence': float(self.confidence),
            'details': details,
            'kpi_snapshot': self.kpi_snapshot
        }


class ThresholdDetector:
    """Deteksi anomali berbasis threshold (rule-based)"""
    
    def __init__(self, config: Dict):
        self.kpi_ranges = config.get('kpi_ranges', {})
        # Counter untuk memastikan serangan flood bersifat stagnan/terus-menerus
        self.ul_flood_consecutive = 0
        
    def detect(self, kpi: KPIMetrics) -> List[Tuple[AnomalyType, Severity, str]]:
        """
        Deteksi anomali berdasarkan threshold
        
        Returns:
            List of (anomaly_type, severity, detail_message)
        """
        anomalies = []
        
        # Signaling Storm Check (RRC Flood)
        rrc_threshold = self.kpi_ranges.get('rrc_connections', {}).get('anomaly_threshold', 500)
        if kpi.rrc_connections > rrc_threshold:
            severity = Severity.CRITICAL if kpi.rrc_connections > 800 else Severity.HIGH
            anomalies.append((
                AnomalyType.SIGNALING_STORM,
                severity,
                f"RRC connections {kpi.rrc_connections} exceeds threshold {rrc_threshold}"
            ))
        
        # UE Attach Rate Check (Signaling Storm)
        attach_threshold = self.kpi_ranges.get('ue_attach_rate', {}).get('anomaly_threshold', 50)
        if kpi.ue_attach_rate > attach_threshold:
            severity = Severity.HIGH
            anomalies.append((
                AnomalyType.SIGNALING_STORM,
                severity,
                f"UE attach rate {kpi.ue_attach_rate:.1f}/min exceeds threshold {attach_threshold}"
            ))
            
        # RRC Flood Fallback (Zero-Config MAC heuristics)
        # Jika PRB terbakar (> 20%), tetapi tidak ada muatan payload manusia (Throughput MAC UL/DL < 100 kbps)
        # Berarti saluran PUSCH/PRACH dipenuhi oleh inisiasi kendali kosong (RRC Flood)
        if kpi.prb_utilization > 20.0 and kpi.ul_throughput < 100.0 and kpi.dl_throughput < 100.0:
            self.sig_storm_consecutive = getattr(self, 'sig_storm_consecutive', 0) + 1
        else:
            self.sig_storm_consecutive = 0
            
        if getattr(self, 'sig_storm_consecutive', 0) >= 3:
            anomalies.append((
                AnomalyType.SIGNALING_STORM,
                Severity.CRITICAL,
                f"Signaling Storm (MAC): Sustained PRB {kpi.prb_utilization:.1f}% with <100kbps payload for 3s"
            ))
        
        # Burst Interference Check (Jamming) - High PRB but terrible signal quality (SNR drop)
        prb_jam_threshold = 80.0
        
        # Ekstrak SNR dari raw_lstm_features menggunakan index schema baru
        snr = 30.0  # Default normal SNR
        if hasattr(kpi, 'raw_lstm_features') and len(kpi.raw_lstm_features) > _IDX_SNR:
            snr = float(kpi.raw_lstm_features[_IDX_SNR])
            
        if kpi.prb_utilization > prb_jam_threshold and snr < 13.0:
            anomalies.append((
                AnomalyType.JAMMING,
                Severity.CRITICAL,
                f"Burst Interference (Jamming): PRB > {prb_jam_threshold}% with critical SNR drop ({snr:.1f} dB)"
            ))
            
        # Uplink Flood Check - Membutuhkan Throughput Ekstrem + PRB mentok + CQI mentok 3 detik!
        # ul_throughput di KPIMetrics dalam Mbps (bukan kbps), threshold 15 Mbps
        ul_high_mbps = self.kpi_ranges.get('ul_throughput', {}).get('anomaly_high_mbps', 15.0)
        
        # Ekstrak CQI dari raw_lstm_features menggunakan index schema baru
        cqi = 15  # Default jika jalan di virtual
        if hasattr(kpi, 'raw_lstm_features') and len(kpi.raw_lstm_features) > _IDX_CQI:
            cqi = int(kpi.raw_lstm_features[_IDX_CQI])
        
        # Syarat Uplink Flood murni (UDP DDOS):
        # 1. UL > 15 Mbps (ul_throughput di KPIMetrics dalam Mbps)
        # 2. PRB > 80%
        # 3. CQI mentok (15) menandakan radio sehat tapi over-utilized
        # 4. DL < 0.5 Mbps (membedakan dari TCP Speedtest legit yang punya DL ACKs besar)
        if kpi.ul_throughput > ul_high_mbps and kpi.prb_utilization > 80.0 and cqi >= 15 and kpi.dl_throughput < 0.5:
            self.ul_flood_consecutive += 1
        else:
            self.ul_flood_consecutive = 0
            
        if self.ul_flood_consecutive >= 3:
            anomalies.append((
                AnomalyType.UPLINK_FLOOD,
                Severity.CRITICAL,
                f"Uplink Flood: Sustained UL {kpi.ul_throughput:.2f} Mbps, DL < 0.5 Mbps, PRB {kpi.prb_utilization:.1f}%"
            ))
            
        # Downlink Flood Check (Core -> UE overload)
        # dl_throughput di KPIMetrics dalam Mbps, threshold 50 Mbps
        dl_high_mbps = self.kpi_ranges.get('dl_throughput', {}).get('anomaly_high_mbps', 50.0)
        
        # Syarat Downlink Flood murni (UDP DDOS):
        # 1. DL > 50 Mbps
        # 2. PRB > 80% (Channel saturasi)
        # 3. UL < 0.5 Mbps (Tidak ada ACKs TCP yang signifikan seperti saat Speedtest)
        if kpi.dl_throughput > dl_high_mbps and kpi.prb_utilization > 80.0 and kpi.ul_throughput < 0.5:
            self.dl_flood_consecutive = getattr(self, 'dl_flood_consecutive', 0) + 1
        else:
            self.dl_flood_consecutive = 0
            
        if self.dl_flood_consecutive >= 3:
            anomalies.append((
                AnomalyType.DOWNLINK_FLOOD,
                Severity.CRITICAL,
                f"Downlink Flood: Sustained DL {kpi.dl_throughput:.2f} Mbps, UL < 0.5 Mbps, PRB {kpi.prb_utilization:.1f}%"
            ))
            
        # DL Throughput Drop Check (Indikasi masalah routing)
        dl_low = self.kpi_ranges.get('dl_throughput', {}).get('anomaly_low', 0)
        if dl_low > 0 and kpi.dl_throughput < dl_low:
            anomalies.append((
                AnomalyType.THROUGHPUT_ANOMALY,
                Severity.HIGH,
                f"DL throughput {kpi.dl_throughput:.1f} kbps unusually low"
            ))
        
        # Handover Rate Check
        handover_threshold = self.kpi_ranges.get('handover_rate', {}).get('anomaly_threshold', 20)
        if kpi.handover_rate > handover_threshold:
            anomalies.append((
                AnomalyType.UE_ANOMALY,
                Severity.MEDIUM,
                f"Handover rate {kpi.handover_rate:.1f}/min exceeds threshold"
            ))
        
        return anomalies


class AnomalyDetector:
    """
    Main Anomaly Detector
    Kombinasi LSTM-Autoencoder dan Threshold Rules
    """
    
    def __init__(self, config: Dict, lstm_model=None):
        self.config = config
        self.lstm_model = lstm_model
        self.threshold_detector = ThresholdDetector(config)
        
        detection_config = config.get('detection', {})
        self.lstm_enabled = detection_config.get('lstm_enabled', True)
        self.threshold_enabled = detection_config.get('threshold_enabled', True)
        self.seq_len = detection_config.get('sequence_length', 10)
        
        # Buffer untuk sequence
        self._kpi_buffer: List[np.ndarray] = []
        
        # Counter berturut-turut untuk clear buffer setelah serangan selesai
        self._normal_count = 0
        self._normal_flush_threshold = 3  # flush setelah 3 sample normal berturut-turut
        
    def set_lstm_model(self, model, scaler=None):
        """Set trained LSTM model"""
        self.lstm_model = model
        self.scaler = scaler
        
    def _add_to_buffer(self, kpi: KPIMetrics):
        """Add KPI ke buffer menggunakan 12-feature schema production-grade."""
        if hasattr(kpi, 'raw_lstm_features') and kpi.raw_lstm_features is not None:
            feat = np.asarray(kpi.raw_lstm_features, dtype=np.float32)
            # Pastikan dimensi sesuai dengan NUM_FEATURES (12)
            if len(feat) != NUM_FEATURES:
                # Pad atau truncate jika tidak sesuai (backward-compat)
                padded = np.zeros(NUM_FEATURES, dtype=np.float32)
                n = min(len(feat), NUM_FEATURES)
                padded[:n] = feat[:n]
                feat = padded
            self._kpi_buffer.append(feat)
        else:
            # Fallback: isi zero-vector berukuran NUM_FEATURES agar tidak crash
            # di LSTM yang mengharapkan 12 fitur
            self._kpi_buffer.append(np.zeros(NUM_FEATURES, dtype=np.float32))
            
        if len(self._kpi_buffer) > self.seq_len:
            self._kpi_buffer.pop(0)
    
    def _detect_with_lstm(self, kpi: KPIMetrics) -> Tuple[bool, float, bool]:
        """
        Deteksi anomali dengan LSTM-Autoencoder
        
        Returns:
            (is_anomaly, anomaly_score, is_early_warning)
        """
        if self.lstm_model is None:
            return False, 0.0, False
        
        if len(self._kpi_buffer) < self.seq_len:
            return False, 0.0, False
        
        # Prepare sequence
        sequence = np.array(self._kpi_buffer)
        
        # Apply normalization
        if getattr(self, 'scaler', None) is not None:
            sequence_normalized = self.scaler.transform(sequence)
        else:
            # Fallback jika tidak ada scaler
            sequence_normalized = sequence
            
        sequence_tensor = torch.FloatTensor(sequence_normalized).unsqueeze(0)  # (1, seq_len, features)
        
        self.lstm_model.eval()
        
        # 1. Full-window anomaly detection (Slow path)
        window_error = self.lstm_model.compute_reconstruction_error(sequence_tensor).item()
        is_window_anomaly, window_score = self.lstm_model.is_anomaly(window_error)
        
        # 2. Stepwise & Partial-window anomaly detection (Fast path)
        stepwise_errors = self.lstm_model.compute_stepwise_error(sequence_tensor).squeeze(0).numpy()
        
        # Dual-threshold strategy
        high_threshold = self.lstm_model.anomaly_threshold
        low_threshold = self.lstm_model.reconstruction_mean + 1.0 * self.lstm_model.reconstruction_std
        
        is_early_warning = False
        final_score = window_score
        
        # A. Per-timestep detection (Latest timestep fast trigger)
        latest_error = stepwise_errors[-1]
        if latest_error > high_threshold:
            is_early_warning = True
            _, latest_score = self.lstm_model.is_anomaly(latest_error)
            final_score = max(window_score, latest_score)
            
        # B. Partial-window detection (e.g. >30% window anomalous)
        anomaly_ratio = np.mean(stepwise_errors > low_threshold)
        if anomaly_ratio > 0.3:
            is_early_warning = True
            
        is_anomaly = is_window_anomaly or is_early_warning
        
        return is_anomaly, final_score, is_early_warning
    
    def detect(self, kpi: KPIMetrics) -> AnomalyResult:
        """
        Deteksi anomali dari KPI metrics
        
        Returns:
            AnomalyResult dengan detail deteksi
        """
        self._add_to_buffer(kpi)
        
        # Threshold-based detection
        threshold_anomalies = []
        if self.threshold_enabled:
            threshold_anomalies = self.threshold_detector.detect(kpi)
        
        # LSTM-based detection
        lstm_is_anomaly = False
        lstm_score = 0.0
        lstm_is_early = False
        if self.lstm_enabled and self.lstm_model is not None:
            lstm_is_anomaly, lstm_score, lstm_is_early = self._detect_with_lstm(kpi)
        
        # Combine results
        is_anomaly = len(threshold_anomalies) > 0 or lstm_is_anomaly
        
        # Determine anomaly type and severity
        if not is_anomaly:
            # Increment consecutive-normal counter
            self._normal_count += 1
            # FLUSH buffer setelah N sample normal berturut-turut.
            # Ini mencegah false positive setelah serangan berhenti karena
            # sisa data serangan di dalam buffer tidak akan "mewarnai" jendela
            # LSTM lagi.
            if self._normal_count >= self._normal_flush_threshold:
                self._kpi_buffer.clear()
                self._normal_count = 0
            return AnomalyResult(
                timestamp=kpi.timestamp,
                is_anomaly=False,
                anomaly_type=AnomalyType.NORMAL,
                severity=Severity.LOW,
                confidence=1.0 - abs(lstm_score) if lstm_score < 0 else 1.0,
                details={
                    'lstm_score': float(lstm_score),
                    'threshold_triggers': []
                },
                kpi_snapshot=kpi.to_dict()
            )
        
        # Ada anomali — reset counter normal
        self._normal_count = 0
        
        # Prioritize threshold anomalies (more interpretable)
        if threshold_anomalies:
            # Sort by severity
            threshold_anomalies.sort(key=lambda x: x[1].value, reverse=True)
            primary_anomaly = threshold_anomalies[0]
            
            return AnomalyResult(
                timestamp=kpi.timestamp,
                is_anomaly=True,
                anomaly_type=primary_anomaly[0],
                severity=primary_anomaly[1],
                confidence=min(1.0, abs(lstm_score) / 3) if lstm_score > 0 else 0.8,
                details={
                    'lstm_score': float(lstm_score),
                    'threshold_triggers': [
                        {'type': a[0].value, 'severity': a[1].name, 'detail': a[2]}
                        for a in threshold_anomalies
                    ]
                },
                kpi_snapshot=kpi.to_dict()
            )
        
        # Only LSTM detected anomaly
        return AnomalyResult(
            timestamp=kpi.timestamp,
            is_anomaly=True,
            anomaly_type=AnomalyType.UNKNOWN,
            severity=Severity.HIGH if lstm_score > 3 else Severity.MEDIUM,
            confidence=min(1.0, abs(lstm_score) / 3),
            details={
                'lstm_score': float(lstm_score),
                'threshold_triggers': [],
                'detection_method': 'LSTM-Autoencoder (Early Warning)' if lstm_is_early else 'LSTM-Autoencoder'
            },
            kpi_snapshot=kpi.to_dict()
        )

