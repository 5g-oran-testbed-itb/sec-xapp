#ifndef UE_TRACKER_H
#define UE_TRACKER_H

#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>

#define UE_MAX_TRACKED      64
#define UE_ALERT_CONSECUTIVE 3   /* alerts fire after this many consecutive periods */

typedef enum {
    UE_NORMAL      = 0,
    UE_UL_FLOOD    = 1,   /* per-UE UL throughput spike */
    UE_DL_FLOOD    = 2,   /* per-UE DL throughput spike */
    UE_SIGNALING   = 3,   /* short-lived RNTI = RRC signaling probe/storm */
} ue_threat_t;

/*
 * State for one RNTI.  Updated every 1 ms MAC period, evaluated every 1 s KPM period.
 * "active" is true if the RNTI appeared in the current 1-second window.
 */
typedef struct {
    uint32_t    rnti;
    bool        active;
    uint64_t    first_seen_ms;
    uint64_t    last_seen_ms;
    uint32_t    periods_seen;       /* cumulative 1-second periods with activity */

    /* peak metrics within current 1-second window */
    float       dl_mbps;
    float       ul_mbps;
    uint32_t    dl_prb;
    uint32_t    ul_prb;
    float       snr;
    float       cqi;
    int64_t     bsr;                /* buffer status report — pending UL bytes */

    ue_threat_t threat;
    uint32_t    consecutive_alerts;
} ue_entry_t;

typedef struct {
    ue_entry_t  ue[UE_MAX_TRACKED];
    int         n;

    /* per-1s churn counters (reset each flush) */
    uint32_t    new_this_period;
    uint32_t    lost_this_period;

    /* thresholds */
    float       thr_ul_mbps;          /* UL flood per UE  (default 20 Mbps) */
    float       thr_dl_mbps;          /* DL flood per UE  (default 50 Mbps) */
    uint32_t    thr_new_rnti_per_sec; /* RRC flood cell   (default 3 new/s) */
    uint32_t    thr_min_periods;      /* short-lived UE   (default 2 periods) */

    /* optional alert log file (NULL → stderr only) */
    FILE       *alert_fp;
} ue_tracker_t;

/* Call once at startup */
void ue_tracker_init(ue_tracker_t *t, FILE *alert_fp);

/*
 * Call from sm_cb_mac for each UE in the MAC report.
 * Must be called under the existing mtx lock (same as rest of sm_cb_mac).
 */
void ue_tracker_mac_update(ue_tracker_t *t,
                           uint32_t rnti,
                           float    dl_mbps,
                           float    ul_mbps,
                           uint32_t dl_prb,
                           uint32_t ul_prb,
                           float    snr,
                           float    cqi,
                           int64_t  bsr,
                           uint64_t now_ms);

/*
 * Call once per KPM 1-second period (from sm_cb_kpm DU branch).
 * Evaluates all UEs, fires alerts, resets per-period state.
 * Must be called under the existing mtx lock.
 */
void ue_tracker_flush(ue_tracker_t *t, uint64_t now_ms);

/* Lookup an entry by RNTI (returns NULL if not found) */
ue_entry_t *ue_tracker_find(ue_tracker_t *t, uint32_t rnti);

#endif /* UE_TRACKER_H */
