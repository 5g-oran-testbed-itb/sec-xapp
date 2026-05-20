# Handover: Implementasi KPM Style 4 Per-UE CSV Storage

**Tanggal**: 14 Mei 2026  
**Proyek**: Security xApp O-RAN (thesis T50)  
**Tujuan**: Menambahkan pencatatan CSV per-RNTI dari KPM Style 4 (FORMAT_3 Indication) ke file `/home/telmat/sec-xapp/csv/per_ue_training_YYYYMMDD_HHMMSS.csv`

---

## 1. Konteks & Motivasi

Saat ini CSV training direkam dari **KPM Style 1 (FORMAT_1)** yang menghasilkan metrik **cell-level aggregated** — semua UE digabung jadi satu baris. Ini menyebabkan:
- LSTM tidak bisa membedakan UE attacker vs UE normal jika ada ≥2 UE
- FPR naik dari 0.44% (1 UE) → 12.67% (2 UE) karena fitur tercampur

**Solusi**: Aktifkan KPM Style 4 → FORMAT_3 Indication → satu baris CSV **per RNTI per epoch**.

Referensi lengkap: [`security-xapp/FEATURE_LIMITATIONS_AND_FUTURE_WORK.md`](security-xapp/FEATURE_LIMITATIONS_AND_FUTURE_WORK.md)

---

## 2. State Sistem Saat Ini

### File yang Relevan

| File | Path | Keterangan |
|------|------|-----------|
| C xApp source | `/home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c` | File utama yang akan diedit |
| KPM config | `/home/telmat/xapp/security-xapp/my_xapp_kpm.conf` | Ganti `format = 1` → `format = 4` |
| sec-xapp dir | `/home/telmat/sec-xapp/` | Working dir xApp native C |
| CSV output dir | `/home/telmat/sec-xapp/csv/` | Sudah dibuat — target output CSV |
| Build dir | `/home/telmat/flexric/build/` | `cmake --build . --target xapp_sec_moni` |

### Apa yang Sudah Ada di xapp_sec_moni.c

**FORMAT_3 handler** — sudah ada di line 927–973. Menerima per-UE data, mengisi `ue_buffers[]`, lalu memanggil `run_inference()`. **Namun tidak ada csv_write di sini.**

```c
// line 927
} else if (kpm->msg.type == FORMAT_3_INDICATION_MESSAGE) {
    kpm_ind_msg_format_3_t const* msg_frm_3 = &kpm->msg.frm_3;
    for (size_t i = 0; i < msg_frm_3->ue_meas_report_lst_len; i++) {
        uint32_t rnti = 9999;
        if (...GNB_UE_ID_E2SM)
            rnti = msg_frm_3->...gnb.amf_ue_ngap_id;   // RNTI dari sini
        
        // Saat ini hanya mengisi 4 fitur:
        // features[t][0] = DRB.UEThpDl / 1000.0f
        // features[t][1] = DRB.UEThpUl / 1000.0f
        // features[t][2] = RRU.PrbUsedDl / 100.0f
        // features[t][3] = RRU.PrbUsedUl / 100.0f
        // features[t][4] = 20.0f  ← HARDCODED (placeholder CQI)
        // features[t][6] = 1.0f   ← HARDCODED (placeholder)
    }
}
```

**csv_trainer_t** — sudah ada (line 567–577), digunakan untuk cell-level CSV.  
CSV header saat ini (FORMAT_1 cell-level):
```
timestamp_ms,datetime,prb_usage_dl_ratio,prb_usage_ul_ratio,cqi,rach_preamble,
air_delay_ul,prb_direction,prb_total,prb_dl_delta,prb_ul_delta,prb_burst_index,label
```

**fill_report_style_4()** — sudah ada di line 344–351. Digunakan saat `format = 4` di config.

**Konstanta penting:**
```c
#define MAX_UE       10   // line 53
#define WINDOW_SIZE  10   // line 54  
#define NUM_FEATURES 12   // line 55
```

---

## 3. Yang Perlu Dilakukan (Implementasi)

### Step 1 — Ganti format di KPM config

File: `/home/telmat/xapp/security-xapp/my_xapp_kpm.conf`

```
# SEBELUM:
format = 1

# SESUDAH:
format = 4
```

> **PENTING**: Setelah ganti format=4, xApp akan menerima FORMAT_3 indication (per-UE) bukan FORMAT_1 (cell-level). Cell-level CSV (g_csv) tidak akan terisi lagi karena callback masuk ke branch FORMAT_3.

### Step 2 — Tambah struct csv_per_ue_trainer_t

Tambahkan setelah definisi `csv_trainer_t` (sekitar line 577):

```c
/* Per-UE CSV recorder — one global file, RNTI as column */
typedef struct {
    FILE*  fp;
    float  prev_prb_dl[MAX_UE];   /* per ue_idx delta tracking */
    float  prev_prb_ul[MAX_UE];
    int    label;
} csv_per_ue_trainer_t;

static csv_per_ue_trainer_t g_csv_per_ue = {0};
```

### Step 3 — Tambah csv_per_ue_open() dan csv_per_ue_write()

Tambahkan setelah fungsi `csv_trainer_close()` (sekitar line 716):

```c
static void csv_per_ue_open(csv_per_ue_trainer_t* t, const char* path, int label)
{
    t->fp = fopen(path, "w");
    if (!t->fp) {
        printf("[CSV_UE] ERROR: cannot open %s\n", path);
        return;
    }
    t->label = label;
    memset(t->prev_prb_dl, 0, sizeof(t->prev_prb_dl));
    memset(t->prev_prb_ul, 0, sizeof(t->prev_prb_ul));
    fprintf(t->fp,
        "timestamp_ms,datetime,rnti,"
        "prb_usage_dl_ratio,prb_usage_ul_ratio,"
        "cqi,rach_preamble,"
        "prb_direction,prb_total,"
        "prb_dl_delta,prb_ul_delta,"
        "label\n");
    fflush(t->fp);
    printf("[CSV_UE] Recording per-UE to %s  (label=%d)\n", path, label);
}

static void csv_per_ue_write(csv_per_ue_trainer_t* t, uint32_t rnti, int ue_idx,
                              float prb_dl_raw, float prb_ul_raw,
                              float cqi, float rach)
{
    if (!t->fp) return;
    maybe_reload_label();   /* hot-label reload juga berlaku di sini */

    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    long long ts_ms = (long long)ts.tv_sec * 1000LL + ts.tv_nsec / 1000000LL;
    char datetime[32];
    struct tm* tm_info = localtime(&ts.tv_sec);
    int ms = (int)(ts.tv_nsec / 1000000LL);
    strftime(datetime, sizeof(datetime), "%Y-%m-%d %H:%M:%S", tm_info);
    snprintf(datetime + 19, sizeof(datetime) - 19, ".%03d", ms);

    static const float EPS = 1e-6f;
    float prb_total     = prb_dl_raw + prb_ul_raw;
    float prb_direction = (prb_ul_raw - prb_dl_raw) / (prb_total + EPS);
    float prb_dl_delta  = prb_dl_raw - t->prev_prb_dl[ue_idx];
    float prb_ul_delta  = prb_ul_raw - t->prev_prb_ul[ue_idx];
    t->prev_prb_dl[ue_idx] = prb_dl_raw;
    t->prev_prb_ul[ue_idx] = prb_ul_raw;

    fprintf(t->fp,
        "%lld,%s,%u,"
        "%.6f,%.6f,"
        "%.3f,%.3f,"
        "%.6f,%.6f,"
        "%.6f,%.6f,"
        "%d\n",
        ts_ms, datetime, rnti,
        prb_dl_raw, prb_ul_raw,
        cqi, rach,
        prb_direction, prb_total,
        prb_dl_delta, prb_ul_delta,
        g_label);
    fflush(t->fp);
}

static void csv_per_ue_close(csv_per_ue_trainer_t* t)
{
    if (t->fp) {
        fclose(t->fp);
        t->fp = NULL;
        printf("[CSV_UE] Per-UE recording stopped.\n");
    }
}
```

### Step 4 — Lengkapi FORMAT_3 handler untuk ekstrak CQI & RACH, lalu panggil csv_per_ue_write

Ganti isi loop FORMAT_3 (sekitar line 940–970) — tambahkan variabel `cqi` dan `rach`, isi dari metric name, dan panggil `csv_per_ue_write` setelah loop inner:

```c
} else if (kpm->msg.type == FORMAT_3_INDICATION_MESSAGE) {
    kpm_ind_msg_format_3_t const* msg_frm_3 = &kpm->msg.frm_3;
    for (size_t i = 0; i < msg_frm_3->ue_meas_report_lst_len; i++) {
        uint32_t rnti = 9999;
        if (msg_frm_3->meas_report_per_ue[i].ue_meas_report_lst.type == GNB_UE_ID_E2SM)
            rnti = msg_frm_3->meas_report_per_ue[i].ue_meas_report_lst.gnb.amf_ue_ngap_id;

        int ue_idx = rnti % MAX_UE;
        int t = ue_buffers[ue_idx].count;

        /* --- NEW: per-UE metric extraction with CQI & RACH --- */
        float prb_dl = 0.0f, prb_ul = 0.0f, cqi_ue = 15.0f, rach_ue = 0.0f;

        if (t < WINDOW_SIZE) {
            for (int f = 0; f < NUM_FEATURES; f++) ue_buffers[ue_idx].features[t][f] = 0.0f;

            kpm_ind_msg_format_1_t const* msg_frm_1 =
                &msg_frm_3->meas_report_per_ue[i].ind_msg_format_1;
            for (size_t j = 0; j < msg_frm_1->meas_data_lst_len; j++) {
                for (size_t z = 0; z < msg_frm_1->meas_data_lst[j].meas_record_len; z++) {
                    if (msg_frm_1->meas_info_lst_len > 0 &&
                        msg_frm_1->meas_info_lst[z].meas_type.type == NAME_MEAS_TYPE)
                    {
                        char name[64];
                        int len = msg_frm_1->meas_info_lst[z].meas_type.name.len;
                        if (len >= 64) len = 63;
                        memcpy(name, msg_frm_1->meas_info_lst[z].meas_type.name.buf, len);
                        name[len] = '\0';

                        float val = 0.0f;
                        if (msg_frm_1->meas_data_lst[j].meas_record_lst[z].value == REAL_MEAS_VALUE)
                            val = msg_frm_1->meas_data_lst[j].meas_record_lst[z].real_val;
                        else if (msg_frm_1->meas_data_lst[j].meas_record_lst[z].value == INTEGER_MEAS_VALUE)
                            val = (float)msg_frm_1->meas_data_lst[j].meas_record_lst[z].int_val;

                        if      (strstr(name, "DRB.UEThpDl")    != NULL) { ue_buffers[ue_idx].features[t][0] = val / 1000.0f; }
                        else if (strstr(name, "DRB.UEThpUl")    != NULL) { ue_buffers[ue_idx].features[t][1] = val / 1000.0f; }
                        else if (strstr(name, "RRU.PrbUsedDl")  != NULL) { prb_dl = val / 100.0f; ue_buffers[ue_idx].features[t][2] = prb_dl; }
                        else if (strstr(name, "RRU.PrbUsedUl")  != NULL) { prb_ul = val / 100.0f; ue_buffers[ue_idx].features[t][3] = prb_ul; }
                        else if (strstr(name, "CQI")            != NULL) { cqi_ue = val;  ue_buffers[ue_idx].features[t][4] = val / 15.0f; }
                        else if (strstr(name, "RACH.Preamble")  != NULL) { rach_ue = val; ue_buffers[ue_idx].features[t][5] = val; }
                    }
                }
            }

            ue_buffers[ue_idx].count++;
            if (ue_buffers[ue_idx].count == WINDOW_SIZE)
                run_inference(rnti, ue_idx);
        }

        /* Write one CSV row per RNTI per indication */
        csv_per_ue_write(&g_csv_per_ue, rnti, ue_idx, prb_dl, prb_ul, cqi_ue, rach_ue);
    }
    fflush(stdout);
```

### Step 5 — Buka g_csv_per_ue di main() dan tambahkan defer close

Cari blok inisialisasi `g_csv` di sekitar line 1241–1251, tambahkan pembukaan per-UE CSV setelahnya:

```c
/* Existing cell-level CSV (masih bermanfaat saat format=1 dipakai) */
{
    char csv_path[256];
    time_t now = time(NULL);
    struct tm* tm_info = localtime(&now);
    strftime(csv_path, sizeof(csv_path),
             "/home/telmat/sec-xapp/csv/training_%Y%m%d_%H%M%S.csv",  /* ← sudah diubah */
             tm_info);
    csv_trainer_open(&g_csv, csv_path, g_label, kpm_period_ms);
}
defer({ csv_trainer_close(&g_csv); });

/* NEW: Per-UE CSV untuk format=4 */
{
    char per_ue_path[256];
    time_t now2 = time(NULL);
    struct tm* tm_info2 = localtime(&now2);
    strftime(per_ue_path, sizeof(per_ue_path),
             "/home/telmat/sec-xapp/csv/per_ue_training_%Y%m%d_%H%M%S.csv",
             tm_info2);
    csv_per_ue_open(&g_csv_per_ue, per_ue_path, g_label);
}
defer({ csv_per_ue_close(&g_csv_per_ue); });
```

### Step 6 — Rebuild

```bash
cmake --build /home/telmat/flexric/build --target xapp_sec_moni
```

---

## 4. Pertanyaan Kritis Sebelum Implementasi

### A. Apakah srsRAN mendukung FORMAT_3 Indication untuk KPM Style 4?

Ini belum dikonfirmasi. srsRAN mungkin mengirim FORMAT_1 meskipun xApp request Style 4.  
**Cara test**: Jalankan xApp dengan `format = 4` dan lihat apakah branch `FORMAT_3_INDICATION_MESSAGE` di log aktif.  
Jika masih `FORMAT_1`, srsRAN belum implementasi Style 4 sepenuhnya.

### B. Apakah RNTI dari FORMAT_3 valid atau selalu 9999?

Di kode, fallback `rnti = 9999` jika tipe bukan `GNB_UE_ID_E2SM`. Perlu verifikasi apakah srsRAN mengisi field ini dengan benar.

### C. Apakah per-UE CSV ini untuk training model baru atau analisis saja?

Untuk training LSTM baru diperlukan:
- Rekam benign traffic (tanpa serangan) → dapat CSV normal
- Rekam setiap tipe serangan dengan label berbeda → CSV berlabel
- Retrain `train_lstm.py` dengan skema fitur baru (per-RNTI)

Untuk analisis thesis saja, cukup rekam dan plot distribusi PRB per RNTI.

---

## 5. Fitur yang Tersedia di FORMAT_3 per srsRAN

Berdasarkan list di `my_xapp_kpm.conf`, metric yang di-subscribe adalah:
```
DRB.UEThpDl, DRB.UEThpUl          ← selalu 0 di srsRAN (known limitation)
RRU.PrbUsedDl, RRU.PrbUsedUl      ← RELIABLE ✅
RRU.PrbAvailDl, RRU.PrbAvailUl    ← RELIABLE ✅
DRB.AirIfDelayUl                   ← selalu 40ms (hardcoded di srsRAN)
RACH.PreambleDedCell               ← cell-level, mungkin 0 di per-UE report
CQI                                ← selalu 15 di srsRAN (saturated)
```

Fitur yang realistis untuk per-UE CSV: `RRU.PrbUsedDl`, `RRU.PrbUsedUl`, `RRU.PrbAvailDl`, `RRU.PrbAvailUl`.

---

## 6. Topologi Testbed (Referensi Cepat)

| Node | IP | Software |
|------|-----|---------|
| RAN | `10.91.2.1` | srsRAN gNB + E2 Agent |
| **RIC (di sini)** | `10.91.2.2` | FlexRIC + Security xApp |
| Core | `10.91.2.4` | Open5GS |

**Cara jalankan xApp** (di RIC node):
```bash
cd /home/telmat/sec-xapp
./start_xapp_c.sh          # mode monitoring only
# atau
./start_xapp_c_mitigate.sh # mode deteksi + E2SM-RC PRB throttle
```

**Binary**: `/home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni`

---

## 7. Ringkasan Pekerjaan

| Step | Action | File |
|------|--------|------|
| 1 | Ganti `format = 1` → `format = 4` | `security-xapp/my_xapp_kpm.conf` |
| 2 | Tambah `csv_per_ue_trainer_t` struct + `g_csv_per_ue` global | `xapp_sec_moni.c` ~line 577 |
| 3 | Tambah `csv_per_ue_open()`, `csv_per_ue_write()`, `csv_per_ue_close()` | `xapp_sec_moni.c` ~line 720 |
| 4 | Update FORMAT_3 handler: ekstrak CQI/RACH + panggil csv_per_ue_write | `xapp_sec_moni.c` ~line 927 |
| 5 | Buka `g_csv_per_ue` di main() + defer close | `xapp_sec_moni.c` ~line 1251 |
| 6 | Rebuild binary | `cmake --build /home/telmat/flexric/build --target xapp_sec_moni` |
| 7 | Test: verifikasi file muncul di `/home/telmat/sec-xapp/csv/per_ue_*.csv` | Terminal |
