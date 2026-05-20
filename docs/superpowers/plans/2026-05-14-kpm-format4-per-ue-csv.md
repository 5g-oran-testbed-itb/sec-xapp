# KPM Format 4 Per-UE CSV Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate xApp KPM subscription from Format 1 (cell-level aggregated) to Format 4 (per-UE via FORMAT_3 Indication), adding `csv_per_ue_trainer_t` recorder that writes one CSV row per RNTI per indication to `/home/telmat/sec-xapp/csv/per_ue_training_YYYYMMDD_HHMMSS.csv`.

**Architecture:** Add `csv_per_ue_trainer_t` struct and its open/write/close functions after the existing `csv_trainer_t` block (~line 577). Extend the existing FORMAT_3 indication handler (~line 927) to fully extract CQI and RACH per-UE metrics and call `csv_per_ue_write`. Open the new recorder in `main()` alongside the existing cell-level recorder. Update `my_xapp_kpm.conf` to `format = 4`.

**Tech Stack:** C (xapp_sec_moni.c), FlexRIC E2SM-KPM API, libconfig (`.conf` format), cmake build system.

---

## File Map

| File | Action | What changes |
|------|--------|--------------|
| `/home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c` | Modify | Add `csv_per_ue_trainer_t` + functions; update FORMAT_3 handler; open/close in `main()` |
| `/home/telmat/xapp/security-xapp/my_xapp_kpm.conf` | Modify | `format = 1` → `format = 4` |
| `/home/telmat/sec-xapp/my_xapp_kpm.conf` | Modify (same change) | `format = 1` → `format = 4` |

---

## Task 1: Add `csv_per_ue_trainer_t` struct and global

**Files:**
- Modify: `/home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c` after line 579 (after `static csv_trainer_t g_csv = {0};`)

- [ ] **Step 1: Locate insertion point**

  Verify line 579 contains `static csv_trainer_t g_csv = {0};`:
  ```bash
  sed -n '577,582p' /home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c
  ```
  Expected output includes `csv_trainer_t` and `g_csv`.

- [ ] **Step 2: Insert struct definition and global**

  After `static csv_trainer_t g_csv = {0};` (line 579), insert:
  ```c
  typedef struct {
      FILE*  fp;
      float  prev_prb_dl[MAX_UE];
      float  prev_prb_ul[MAX_UE];
      int    label;
  } csv_per_ue_trainer_t;

  static csv_per_ue_trainer_t g_csv_per_ue = {0};
  ```

- [ ] **Step 3: Verify compilation**

  ```bash
  cmake --build /home/telmat/flexric/build --target xapp_sec_moni 2>&1 | tail -5
  ```
  Expected: `[100%] Built target xapp_sec_moni` with no errors.

---

## Task 2: Add `csv_per_ue_open`, `csv_per_ue_write`, `csv_per_ue_close`

**Files:**
- Modify: `/home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c` after `csv_trainer_close()` (~line 723)

- [ ] **Step 1: Locate insertion point**

  ```bash
  grep -n "csv_trainer_close" /home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c
  ```
  Note the line number of the closing `}` of `csv_trainer_close`.

- [ ] **Step 2: Insert the three functions after `csv_trainer_close`'s closing brace**

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
      maybe_reload_label();

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

- [ ] **Step 3: Verify compilation**

  ```bash
  cmake --build /home/telmat/flexric/build --target xapp_sec_moni 2>&1 | tail -5
  ```
  Expected: no errors.

---

## Task 3: Rewrite FORMAT_3 indication handler to extract CQI/RACH and call csv_per_ue_write

**Files:**
- Modify: `/home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c` lines ~927–973

The current handler only fills 4 features and has hardcoded placeholders (`features[t][4] = 20.0f`). We replace the inner loop body to properly extract all metrics, store them in `ue_buffers`, and call `csv_per_ue_write`.

- [ ] **Step 1: Locate the FORMAT_3 block**

  ```bash
  grep -n "FORMAT_3_INDICATION_MESSAGE" /home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c
  ```

- [ ] **Step 2: Replace the FORMAT_3 handler body**

  Replace the entire `} else if (kpm->msg.type == FORMAT_3_INDICATION_MESSAGE) {` block (from that line through the matching `}` before the `} else {` for unhandled types) with:

  ```c
  } else if (kpm->msg.type == FORMAT_3_INDICATION_MESSAGE) {
      kpm_ind_msg_format_3_t const* msg_frm_3 = &kpm->msg.frm_3;
      for (size_t i = 0; i < msg_frm_3->ue_meas_report_lst_len; i++) {
          uint32_t rnti = 9999;
          if (msg_frm_3->meas_report_per_ue[i].ue_meas_report_lst.type == GNB_UE_ID_E2SM)
              rnti = msg_frm_3->meas_report_per_ue[i].ue_meas_report_lst.gnb.amf_ue_ngap_id;

          int ue_idx = rnti % MAX_UE;
          int t = ue_buffers[ue_idx].count;

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

          csv_per_ue_write(&g_csv_per_ue, rnti, ue_idx, prb_dl, prb_ul, cqi_ue, rach_ue);
      }
      fflush(stdout);
  ```

  **Key changes vs old code:**
  - `prb_dl` and `prb_ul` variables capture the normalized PRB values as they're parsed
  - `cqi_ue` defaults to `15.0f` (connected), `rach_ue` defaults to `0.0f`
  - Removed hardcoded `features[t][4] = 20.0f` and `features[t][6] = 1.0f` placeholders
  - Added CQI extraction: `features[t][4] = val / 15.0f` (normalized)
  - Added RACH extraction: `features[t][5] = val`
  - `csv_per_ue_write` called after the inner loop

- [ ] **Step 3: Verify compilation**

  ```bash
  cmake --build /home/telmat/flexric/build --target xapp_sec_moni 2>&1 | tail -5
  ```
  Expected: no errors.

---

## Task 4: Open `g_csv_per_ue` in `main()` and add defer close

**Files:**
- Modify: `/home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c` ~line 1251

- [ ] **Step 1: Locate `defer({ csv_trainer_close(&g_csv); });` in main()**

  ```bash
  grep -n "csv_trainer_close\|csv_trainer_open" /home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c | grep -v "^[0-9]*:static"
  ```
  This shows the two calls in `main()`.

- [ ] **Step 2: After the existing `defer({ csv_trainer_close(&g_csv); });` line, insert**

  ```c
  /* Per-UE CSV for format=4 */
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

- [ ] **Step 3: Verify compilation**

  ```bash
  cmake --build /home/telmat/flexric/build --target xapp_sec_moni 2>&1 | tail -5
  ```
  Expected: no errors.

---

## Task 5: Switch KPM config to format = 4

> **Note:** Both `/home/telmat/xapp/security-xapp/my_xapp_kpm.conf` and `/home/telmat/sec-xapp/my_xapp_kpm.conf` need updating. The xApp binary reads from whichever path is passed with `-c`. The `start_xapp_c.sh` script uses the one in `sec-xapp/`.

- [ ] **Step 1: Update `/home/telmat/xapp/security-xapp/my_xapp_kpm.conf`**

  Change line 15:
  ```
  format = 1,
  ```
  to:
  ```
  format = 4,
  ```

- [ ] **Step 2: Update `/home/telmat/sec-xapp/my_xapp_kpm.conf`**

  Same change: `format = 1,` → `format = 4,`

- [ ] **Step 3: Verify both files**

  ```bash
  grep "format" /home/telmat/xapp/security-xapp/my_xapp_kpm.conf
  grep "format" /home/telmat/sec-xapp/my_xapp_kpm.conf
  ```
  Expected: both show `format = 4,`

---

## Task 6: Final build and smoke-test

- [ ] **Step 1: Full rebuild**

  ```bash
  cmake --build /home/telmat/flexric/build --target xapp_sec_moni -j$(nproc) 2>&1 | tail -10
  ```
  Expected: `[100%] Built target xapp_sec_moni`

- [ ] **Step 2: Verify binary updated**

  ```bash
  ls -la /home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni
  ```
  Check that mtime is current (today's date and time).

- [ ] **Step 3: Run --test to verify CSV writer still passes**

  ```bash
  /home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni --test
  ```
  Expected output:
  ```
  [PASS] test_csv_writer
  ```

- [ ] **Step 4: Verify csv/ output directory exists**

  ```bash
  ls -la /home/telmat/sec-xapp/csv/
  ```
  If missing: `mkdir -p /home/telmat/sec-xapp/csv/`

- [ ] **Step 5: Post-run check (when testbed is live)**

  After running the xApp with the testbed connected:
  ```bash
  ls -la /home/telmat/sec-xapp/csv/per_ue_training_*.csv
  head -5 /home/telmat/sec-xapp/csv/per_ue_training_*.csv
  ```
  Expected: File exists, header row contains `rnti` column, data rows have non-9999 RNTI values if srsRAN fills `gnb.amf_ue_ngap_id`.

  If all RNTI values are `9999`, srsRAN is not filling `GNB_UE_ID_E2SM` in FORMAT_3 responses — this is a known srsRAN limitation (see handover doc §4A). The file will still record correctly but RNTI won't be meaningful.

  Check which indication branch fires:
  ```bash
  # In xApp output, look for FORMAT_3 or FORMAT_1 messages:
  # FORMAT_3 fires → per_ue CSV gets rows
  # FORMAT_1 fires → cell-level CSV gets rows, per_ue CSV stays empty (srsRAN doesn't support Style 4)
  ```

---

## Known Risks / Fallback

| Risk | Mitigation |
|------|-----------|
| srsRAN sends FORMAT_1 even with `format=4` | FORMAT_3 handler is extended but FORMAT_1 handler unchanged — cell-level CSV continues. Both files open regardless of which branch fires. |
| srsRAN fills `rnti = 9999` (amf_ue_ngap_id not set) | CSV still records correctly; RNTI column shows 9999. Useful for analysis even if not per-UE separable. |
| gNB rejects FORMAT_4 subscription | xApp will log subscription failure. Revert `format = 1` in conf to restore cell-level recording. |
| `csv/` directory missing | `mkdir -p /home/telmat/sec-xapp/csv/` before running. |
