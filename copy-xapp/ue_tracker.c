#include "ue_tracker.h"
#include <string.h>
#include <stdlib.h>

/* ------------------------------------------------------------------ */
/* Internal helpers                                                     */
/* ------------------------------------------------------------------ */

static void alert(ue_tracker_t *t, const char *msg)
{
    fputs(msg, stderr);
    fflush(stderr);
    if (t->alert_fp) {
        fputs(msg, t->alert_fp);
        fflush(t->alert_fp);
    }
}

static ue_entry_t *find_slot(ue_tracker_t *t, uint32_t rnti, uint64_t now_ms)
{
    for (int i = 0; i < t->n; i++)
        if (t->ue[i].rnti == rnti)
            return &t->ue[i];

    /* allocate new slot */
    ue_entry_t *e = NULL;
    if (t->n < UE_MAX_TRACKED) {
        e = &t->ue[t->n++];
    } else {
        /* table full: evict oldest inactive entry */
        for (int i = 0; i < t->n; i++)
            if (!t->ue[i].active &&
                (!e || t->ue[i].last_seen_ms < e->last_seen_ms))
                e = &t->ue[i];
        if (!e) e = &t->ue[0];   /* worst case */
    }

    memset(e, 0, sizeof(*e));
    e->rnti          = rnti;
    e->first_seen_ms = now_ms;
    t->new_this_period++;
    return e;
}

/* ------------------------------------------------------------------ */
/* Public API                                                           */
/* ------------------------------------------------------------------ */

void ue_tracker_init(ue_tracker_t *t, FILE *alert_fp)
{
    memset(t, 0, sizeof(*t));
    t->thr_ul_mbps          = 20.0f;
    t->thr_dl_mbps          = 50.0f;
    t->thr_new_rnti_per_sec = 3;
    t->thr_min_periods      = 2;
    t->alert_fp             = alert_fp;
}

ue_entry_t *ue_tracker_find(ue_tracker_t *t, uint32_t rnti)
{
    for (int i = 0; i < t->n; i++)
        if (t->ue[i].rnti == rnti)
            return &t->ue[i];
    return NULL;
}

void ue_tracker_mac_update(ue_tracker_t *t,
                           uint32_t rnti,
                           float    dl_mbps,
                           float    ul_mbps,
                           uint32_t dl_prb,
                           uint32_t ul_prb,
                           float    snr,
                           float    cqi,
                           int64_t  bsr,
                           uint64_t now_ms)
{
    ue_entry_t *e = find_slot(t, rnti, now_ms);
    e->active       = true;
    e->last_seen_ms = now_ms;
    /* keep peak values within the 1-second window */
    if (dl_mbps > e->dl_mbps) e->dl_mbps = dl_mbps;
    if (ul_mbps > e->ul_mbps) e->ul_mbps = ul_mbps;
    if (dl_prb  > e->dl_prb)  e->dl_prb  = dl_prb;
    if (ul_prb  > e->ul_prb)  e->ul_prb  = ul_prb;
    e->snr = snr;
    e->cqi = cqi;
    e->bsr = bsr;
}

void ue_tracker_flush(ue_tracker_t *t, uint64_t now_ms)
{
    char buf[256];

    /* ---- cell-level RRC flood check -------------------------------- */
    if (t->new_this_period >= t->thr_new_rnti_per_sec) {
        snprintf(buf, sizeof(buf),
            "[CELL ALERT][RRC FLOOD] %u new RNTI(s) in 1s"
            " (threshold=%u, total_active=%d)\n",
            t->new_this_period, t->thr_new_rnti_per_sec, t->n);
        alert(t, buf);
    }

    /* ---- per-UE evaluation ----------------------------------------- */
    int lost = 0;
    for (int i = 0; i < t->n; i++) {
        ue_entry_t *e = &t->ue[i];

        if (!e->active) {
            /* UE disappeared this period */
            if (e->periods_seen > 0 && e->periods_seen < t->thr_min_periods) {
                uint64_t lifetime = (e->last_seen_ms > e->first_seen_ms)
                                    ? e->last_seen_ms - e->first_seen_ms : 0;
                snprintf(buf, sizeof(buf),
                    "[UE ALERT][SIGNALING STORM] RNTI=0x%04x"
                    " disappeared after %u period(s) lifetime=%llums\n",
                    e->rnti, e->periods_seen, (unsigned long long)lifetime);
                alert(t, buf);
            }
            lost++;
            continue;
        }

        /* ---- per-UE metric thresholds -------------------------------- */
        ue_threat_t threat = UE_NORMAL;
        if      (e->ul_mbps > t->thr_ul_mbps) threat = UE_UL_FLOOD;
        else if (e->dl_mbps > t->thr_dl_mbps) threat = UE_DL_FLOOD;

        e->threat = threat;
        if (threat != UE_NORMAL) {
            e->consecutive_alerts++;
            if (e->consecutive_alerts >= UE_ALERT_CONSECUTIVE) {
                snprintf(buf, sizeof(buf),
                    "[UE ALERT][%s] RNTI=0x%04x"
                    " ul=%.2fMbps dl=%.2fMbps prb_ul=%u prb_dl=%u"
                    " snr=%.1f cqi=%.0f bsr=%lld consecutive=%u\n",
                    threat == UE_UL_FLOOD ? "UL_FLOOD" : "DL_FLOOD",
                    e->rnti,
                    e->ul_mbps, e->dl_mbps,
                    e->ul_prb, e->dl_prb,
                    e->snr, e->cqi, (long long)e->bsr,
                    e->consecutive_alerts);
                alert(t, buf);
            }
        } else {
            e->consecutive_alerts = 0;
        }

        e->periods_seen++;
        /* reset peak metrics for next 1-second window */
        e->active  = false;
        e->dl_mbps = 0.0f;
        e->ul_mbps = 0.0f;
        e->dl_prb  = 0;
        e->ul_prb  = 0;
    }

    t->lost_this_period = (uint32_t)lost;
    t->new_this_period  = 0;
    (void)now_ms;
}
