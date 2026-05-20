#ifndef SEC_IDS_H
#define SEC_IDS_H

#include <stdint.h>

/*
 * Snapshot metrik sel per window KPM.
 * Diisi oleh sm_cb_kpm() di xapp_sec_moni.c,
 * lalu diserahkan ke rule_based_detect().
 */
typedef struct {
    /* --- Radio Resource (DU KPM) --- */
    float prb_used_dl;      /* RRU.PrbUsedDl            (%)      */
    float prb_used_ul;      /* RRU.PrbUsedUl            (%)      */
    float prb_tot_dl;       /* RRU.PrbTotDl             (count)  */
    float prb_tot_ul;       /* RRU.PrbTotUl             (count)  */
    float prb_avail_dl;     /* RRU.PrbAvailDl           (count)  */
    float prb_avail_ul;     /* RRU.PrbAvailUl           (count)  */
    /* --- Throughput --- */
    float thp_dl_mbps;      /* DRB.UEThpDl / 1000       (Mbps)   */
    float thp_ul_mbps;      /* DRB.UEThpUl / 1000       (Mbps)   */
    /* --- RACH / Delay --- */
    float rach_preamble;    /* RACH.PreambleDedCell     (count)  */
    float air_delay_ul;     /* DRB.AirIfDelayUl         (ms)     */
    /* --- RLC Volume / Quality --- */
    float rlc_vol_dl;       /* DRB.RlcSduTransmittedVolumeDL     */
    float rlc_vol_ul;       /* DRB.RlcSduTransmittedVolumeUL     */
    float rlc_drop_dl;      /* DRB.RlcPacketDropRateDl  (ratio)  */
    float rlc_delay_ul;     /* DRB.RlcDelayUl           (ms)     */
    /* --- Radio Quality --- */
    float cqi;              /* CQI                      (0-15)   */
    float rsrp;             /* RSRP                     (dBm)    */
    float rsrq;             /* RSRQ                     (dB)     */
    /* --- Control Plane (CU-CP KPM) --- */
    float rrc_att;          /* RRC.ConnEstabAtt         (count)  */
    float rrc_succ;         /* RRC.ConnEstabSucc        (count)  */
    /* --- RRC Storm proxy (srsRAN empty KPM indications during UE detach) --- */
    float empty_ind_rate;   /* #empty APER-failed indications since last window */
} cell_metrics_t;

/* Alert type — ditetapkan oleh Stage 1, digunakan di Stage 2 dan CSV */
typedef enum {
    ALERT_NONE = 0,
    ALERT_UL_SATURATION,
    ALERT_DL_SATURATION,
    ALERT_RRC_STORM,
    ALERT_RADIO_DEGRADATION_SUSPICION,
    ALERT_PERIODIC_BURST_ANOMALY,
} alert_type_t;

/* State deteksi dua-stage — dikembalikan oleh ids_get_detection_state()
 * untuk digunakan csv_trainer_write() saat menulis baris CSV.            */
typedef struct {
    int          stage1_alert;               /* 1 jika Stage 1 WARNING aktif */
    int          stage2_confirmed;           /* 1 jika Stage 2 CRITICAL terkonfirmasi */
    alert_type_t alert_type;                 /* tipe anomali aktif */
    long long    stage1_latency_ms;          /* ms dari mulai anomali ke WARNING pertama */
    long long    stage2_confirmation_time_ms;/* ms dari WARNING pertama ke CRITICAL */
} ids_detection_state_t;

/* Panggil sekali saat startup, berikan period KPM agar alert bisa
 * melaporkan durasi serangan dalam milidetik.                      */
void ids_init(uint64_t period_ms);

/* Reset semua consecutive counter (opsional, saat xApp restart).   */
void ids_reset(void);

/* Evaluasi semua rules terhadap snapshot metrik terbaru.
 * Print langsung ke stdout jika ada anomali.
 * now_ms: epoch ms saat callback dipanggil (dari clock_gettime).
 * Return: 0=normal, 1=WARNING (Stage 1 active, Stage 2 belum confirm),
 *         2=CRITICAL (Stage 2 confirmed, mitigasi authorized).            */
int rule_based_detect(cell_metrics_t const* m, long long now_ms);

/* Kembalikan state deteksi terakhir untuk csv_trainer_write().
 * Harus dipanggil setelah rule_based_detect() pada window yang sama.      */
ids_detection_state_t ids_get_detection_state(void);

#endif /* SEC_IDS_H */
