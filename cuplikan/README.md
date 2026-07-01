# Cuplikan — Berkas Asli untuk Screenshot

Folder ini berisi **berkas sumber asli** (bukan cuplikan yang disederhanakan) yang
menjadi rujukan seluruh cuplikan kode/konfigurasi di [../docs/BAB3.md](../docs/BAB3.md)
dan [../docs/BAB3_CODE_SNIPPETS.md](../docs/BAB3_CODE_SNIPPETS.md).
Disalin per 2026-06-28 dari repo lokal + node testbed (RAN 10.91.2.1, Core 10.91.2.4).

| Berkas | Bagian / Gambar BAB3 | Asal |
|---|---|---|
| `lstm_autoencoder.py` | LSTM-AE (Gambar 3.3 / 3.7, kode model) | `src/detection/` |
| `gru_autoencoder.py` | GRU-AE (Gambar 3.4 / 3.8, kode model) | `src/detection/` |
| `feature_schema_ue.py` | 19 fitur + bobot Scheme A (Tabel 3.1/3.2) | `src/detection/` |
| `export_onnx_ue.py` | Ekspor ONNX LSTM & GRU | repo root |
| `sec_ids_ue.c` | Init ONNX, inferensi, decision engine, R1–R5 | flexric `…/monitor/` |
| `sec_ids_ue.h` | Konstanta R1–R5, ML_SEQ_LEN, ML_NUM_FEATURES | flexric `…/monitor/` |
| `xapp_sec_mitigate.c` | Trigger mitigasi E2SM-RC (Style 2 / Action 6) | flexric `…/monitor/` |
| `xapp_sec_moni.c` | CSV logger per-UE, subscription KPM, throttle | flexric `…/monitor/` |
| `my_xapp_kpm.conf` | Subscription KPM (time=1000, format=4, E42) | repo root |
| `cots_n78_copied.yml` | Konfigurasi gNodeB srsRAN | node RAN `10.91.2.1` |
| `smf.yaml` | Konfigurasi SMF Open5GS (IP pool UE) | node Core `10.91.2.4` |
| `amf.yaml` | Konfigurasi AMF (NGAP/N2, PLMN, TAC) | node Core `10.91.2.4` |
| `upf.yaml` | Konfigurasi UPF (GTP-U/N3, subnet) | node Core `10.91.2.4` |
| `csv_exporter.py` | Prometheus exporter (metrik `xapp_ue_*`) | `exporter/` |
| `prometheus.yml` | Konfigurasi scrape Prometheus | `prometheus/` |
| `docker-compose.yml` | Port: Grafana 3000, Prometheus 9090, exporter 8000, cAdvisor 8081 | repo root |
| `lstm_ue_v4_threshold.json` | Threshold LSTM-AE P97 = 0.025266 | `models/` |
| `gru_ue_v4_threshold.json` | Threshold GRU-AE P97 = 0.025969 | `models/` |

> Catatan: berkas di BAB3 sengaja disederhanakan agar mudah dibaca; berkas di sini
> adalah versi penuh/asli. Untuk konfigurasi gNB, perhatikan `e2sm_rc_enabled` harus
> `true` saat menjalankan demo mitigasi (lihat catatan di BAB3_CODE_SNIPPETS.md §6).
