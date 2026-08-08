# Walkthrough of Compliance Plan Implementation (Tasks 1-7)

All tasks of the [Dashboard Compliance Plan](file:///home/telmat/sec-xapp/docs/superpowers/plans/2026-07-11-dashboard-bab2-compliance.md) have been successfully implemented following a strict Test-Driven Development (TDD) cycle.

## Changes Made

### 1. Prometheus Exporter Changes
Modified [csv_exporter.py](file:///home/telmat/sec-xapp/exporter/csv_exporter.py) to add the metrics and background loops needed to track mitigation events and active ML thresholds:
- **Mitigation Parsers and Handlers**: Added `parse_mitigation_row` and `update_mitigation_metrics` helper functions to parse the honest mitigation events.
- **Mitigation Gauges/Counters**: Registered `xapp_ue_mitigation_active` (gauge), `xapp_ue_mitigation_prb_limit` (gauge), and `xapp_mitigations_applied_total` (counter).
- **Mitigation Tailing**: Implemented `mitigation_tail_loop()` to continuously watch for new `mitigation_events_*.csv` files, tailing them and updating metrics in real-time.
- **Active ML Threshold & Model Gauges**: Registered `xapp_ue_threshold` and `xapp_ue_model_info` gauges.
- **Sidecar Watcher**: Implemented `read_active_threshold()` to read the active ML model name and threshold value from the C sidecar file (`/tmp/xapp_active_threshold`), with a background watcher thread `threshold_watch_loop()`.
- **Thread Spawning**: Registered both `mitigation-tail` and `threshold-watch` threads in `main()`.

### 2. Unit Testing Changes
Modified [test_csv_exporter.py](file:///home/telmat/sec-xapp/exporter/test_csv_exporter.py) to add unit tests covering all new features. A strict TDD cycle was observed where tests were written first, failure was verified (RED phase), minimal implementation was added, and then the tests were verified to pass (GREEN phase):
- **Mitigation CSV Row Parsing**: Added `test_parse_mitigation_row_throttle` and `test_parse_mitigation_row_restore_defaults_prb_100`.
- **Mitigation Metric Updating**: Added `test_update_mitigation_metrics_throttle_then_restore`.
- **Active Threshold Sidecar Reading & Model Switching**: Added `test_read_active_threshold_sidecar`, `test_read_active_threshold_missing_file_noop`, and `test_read_active_threshold_sidecar_switch`.

### 3. C monitor xApp Changes
Modified [xapp_sec_moni.c](file:///home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c) (and synced to [copy-xapp/xapp_sec_moni.c](file:///home/telmat/sec-xapp/copy-xapp/xapp_sec_moni.c)):
- **Mitigation Log Handler**: Declared a global `g_mit_fp` CSV pointer.
- **File Setup**: Opened `mitigation_events_%Y%m%d_%H%M%S.csv` in `main()` with headers.
- **Honest Logging**: Wrote successful control commands inside `ipc_send_mitigate()` immediately after the IPC ACK is received.
- **Threshold Sidecar**: Wrote `/tmp/xapp_active_threshold` on initialization in `init_onnx_ue()` mapping loaded modes (GRU/LSTM) to active threshold values.

### 4. Grafana Dashboard Changes
Modified [per_ue_live.json](file:///home/telmat/sec-xapp/grafana/provisioning/dashboards/per_ue_live.json) dashboard config to reflect the new capabilities:
- **Active MSE Threshold**: Replaced the static GRU-UE v4 threshold line in panel ID 6 with a dynamic query of the `xapp_ue_threshold` metric.
- **Cleaned Avg MSE threshold steps**: Removed the stale static threshold steps in panel ID 12.
- **State Timeline Panel**: Added a new "Riwayat Mitigasi E2SM-RC (PRB Throttle per UE)" state-timeline panel (ID 30) tracking `xapp_ue_mitigation_active`.
- **Mitigation Latency Panel**: Added a "Latensi Keputusan Mitigasi" timeseries panel (ID 31) tracking the detect, confirm, and total transition latency against the 1s constraint.
- **Panel Descriptions**: Added detailed Indonesian descriptions for panel IDs 12, 14, 20, 30, and 31.

### 5. Docker Infrastructure Changes
Modified [docker-compose.yml](file:///home/telmat/sec-xapp/docker-compose.yml):
- **Volume Mounts**: Mounted `/tmp` from host to `/tmp:ro` in `csv-exporter` container to enable reading the active threshold sidecar file.

---

## Verification Results

### Automated Unit Tests
All unit tests in `test_csv_exporter.py` completed successfully:
```bash
python3 -m pytest test_csv_exporter.py -v
```
**Output Summary**:
- Total tests: **38**
- Passed: **38**
- Execution time: **1.04s**

### C xApp Build Validation
Compiled successfully using the build commands:
- Status: **Clean Build**

### End-to-End Verification
1. **Startup Check**: Started RIC and `xapp_sec_moni` in tmux. The C xApp successfully connected to nearRT-RIC.
2. **Active Threshold**: The C monitor successfully wrote `/tmp/xapp_active_threshold` with `0.026026 gru_ue_v5`.
3. **Container Sync**: Checked `/tmp/xapp_active_threshold` inside the `xapp-exporter` container; it loaded and published the values:
   - `xapp_ue_threshold`: `0.026026`
   - `xapp_ue_model_info{model="gru_ue_v5"}`: `1.0`
4. **Mitigation Events Tailing**: Simulated a `THROTTLE` then `RESTORE` event on the generated CSV log; the exporter dynamically updated:
   - `xapp_ue_mitigation_active`: `1.0` → `0.0`
   - `xapp_ue_mitigation_prb_limit`: `0.0` → `100.0`
   - `xapp_mitigations_applied_total`: `1.0`

### JSON Schema Verification
The Grafana dashboard JSON file was validated for syntactic correctness:
- Status: **VALID**

### 6. Dashboard Layout Restructuring & Optimization (Task 8)
Refactored [per_ue_live.json](file:///home/telmat/sec-xapp/grafana/provisioning/dashboards/per_ue_live.json) based on user brainstorming and design lock:
- **Consolidated Row 1 (Status & Deteksi UE)**: Omitted redundant stat cards (ID 3, 12, 13). Grouped Active RNTIs (ID 1, w=2), Alerted RNTIs (ID 2, w=2), Max Severity (ID 11, w=2), UE Aktif Table (ID 14, w=8), Detection Stage Timeline (ID 5, w=5), and MSE Score Graph (ID 6, w=5) into a single row of height 5.
- **Throughput Splitting (Row 2)**: Split the combined Throughput panel (ID 7) into two side-by-side panels: Throughput UL per UE (ID 7, w=12) and Throughput DL per UE (ID 15, w=12) of height 6.
- **PRB Splitting (Row 3)**: Split the combined PRB Utilization panel into PRB Utilization UL per UE (ID 8, w=8), PRB Utilization DL per UE (ID 16, w=8), and PRB Direction & UL Efficiency per UE (ID 9, w=8) side-by-side in Row 3 of height 6.
- **Mitigation Row (Row 4)**: Placed Total Blocked Attacks (ID 20, w=6) and Riwayat Mitigasi E2SM-RC Timeline (ID 30, w=18) side-by-side in Row 4 of height 6.
- **Infratructure & Latency Row (Row 5 - Bottom)**: Placed CPU Resource Overhead (ID 21, w=6), Memory Resource Overhead (ID 22, w=6), and Latensi Keputusan Mitigasi Graph (ID 31, w=12) side-by-side in Row 5 of height 6.
- **Grafana Provisioning & Force Overwrite**: Configured `allowUiUpdates: false` in [dashboards.yml](file:///home/telmat/sec-xapp/grafana/provisioning/dashboards/dashboards.yml) to ensure Grafana forces synchronization from disk, and set 1-second auto-refresh and timeInterval.

---

## Verification of Dashboard Layout
1. Checked dashboard layout coordinates and JSON schema via automated python linter: **PASS**
2. Verified dashboard loading and rendering in Grafana logs: **PASS** (reloads provisioned dashboard successfully upon container restart).

### 7. Offline Evaluation Dashboard Restructuring (Tasks 9-12)
Refactored [testing_app.py](file:///home/telmat/sec-xapp/testing_app/testing_app.py) and Grafana provisioned dashboards to optimize the offline evaluation tool:
- **Iframe Viewport Height Adjustments**: In both [per_ue_eval_tool.json](file:///home/telmat/sec-xapp/grafana/provisioning/dashboards/per_ue_eval_tool.json) and [testing.json](file:///home/telmat/sec-xapp/grafana/provisioning/dashboards/testing.json), increased the Grafana panel height `h` to `72` and the embedded iframe style height to `1600px` to resolve the viewport clipping issue.
- **Split Throughput Charts**: Split the throughput display into two side-by-side graphs: Throughput UL (ID `graph-thp-ul`, w=50%) and Throughput DL (ID `graph-thp-dl`, w=50%).
- **Split PRB Utilization Charts**: Split the original unified PRB chart into two side-by-side graphs: PRB Utilization UL (ID `graph-prb-ul`, w=50%) and PRB Utilization DL (ID `graph-prb-dl`, w=50%) in Row 2, each with the 80% threshold line overlay.
- **Consolidated ROC & Anomaly Score Row**: Combined the ROC curve (ID `graph-roc`, w=50%) and the GRU/LSTM anomaly score timeline (ID `graph-lstm`, w=50%) side-by-side in Row 3 of height 320px.
- **Top-Aligned Per-Stage Comparison Table**: Moved the "Perbandingan Per-Stage" table (ID `stage-table`) from the very bottom of the page to a top-level grid row, aligning it side-by-side (width 11/24) with the stacked quality and latency stat cards (width 13/24) to keep key numerical data high up and clustered together next to ROC-AUC and E2E Detection.
- **Mitigation Latency Update**: Updated the constant `XAPP_CYCLE_MS` from `120.0` to `500.0` (1/2 s) so that the offline simulation reflects the correct mitigation delay.
- **Rebuilt Container**: Rebuilt and restarted the `testing-app` Docker container and restarted `grafana` container to reload provisioning changes.

---

## Verification of Offline Evaluation Dashboard
1. Checked syntax and compiled `testing_app.py`: **PASS**
2. Verified Dash server start and HTTP response via `curl http://localhost:8050`: **PASS (HTTP 200)**
3. Reloaded Grafana and verified dashboards loaded successfully: **PASS**

### 8. Total Blocked Attacks Reset per Run
Modified [csv_exporter.py](file:///home/telmat/sec-xapp/exporter/csv_exporter.py) to enable resetting the blocked attacks counter on each new xApp session/run:
- **Converted Counter to Gauge**: Converted `xapp_mitigations_applied_total` from a Prometheus `Counter` to a `Gauge`. Since Gauges support clearing labeled values dynamically, it allows the value to be reset back to `0`.
- **Reset Trigger**: Added `c_mitigations_applied.clear()` in the `mitigation_tail_loop()` handler whenever a new `mitigation_events_*.csv` file is detected.
- **Unit Testing**: Updated [test_csv_exporter.py](file:///home/telmat/sec-xapp/exporter/test_csv_exporter.py) with the decimal RNTI assertions to verify that all parsing and metric functions continue to pass without regression.

### 9. Perbaikan Logika Lock RNTI pada RESTORE Mitigasi
* **Masalah Utama**: Sebelumnya, target mitigasi `g_throttle_target_ue_id` di [xapp_sec_moni.c](file:///home/telmat/sec-xapp/copy-xapp/xapp_sec_moni.c) ditimpa setiap kali ada alert per-UE, bahkan alert peringatan (`WARNING`) dari UE normal. Hal ini menyebabkan RNTI target terkotori saat mitigasi aktif, sehingga perintah `RESTORE` salah memulihkan UE normal dan membiarkan penyerang asli terbatasi 5% selamanya.
* **Perbaikan**: Mengunci penetapan `g_throttle_target_ue_id` dan `g_pending_throttle` agar hanya dieksekusi ketika mitigasi sedang tidak aktif (`!g_throttle_active`). Target mitigasi dikunci pada RNTI penyerang asli dan tidak dapat ditimpa oleh alert dari UE normal lainnya.
* **Kompilasi**: Melakukan kompilasi ulang pada binari xApp dengan sukses (`rebuild_xapp_user.sh`).

### 10. Pembuatan Ulang Plot Evaluasi Mitigasi dengan Dataset Baru
* **Dataset Baru**: Menggunakan dataset `171404` dengan RNTI 6 (Penyerang) dan RNTI 5 (Normal).
* **Pembaruan Skrip Plot**: Memperbarui [plot_mitigation_20260711.py](file:///home/telmat/sec-xapp/eval_figures/plot_mitigation_20260711.py) (Indonesian) dan [plot_mitigation_english.py](file:///home/telmat/sec-xapp/eval_figures/plot_mitigation_english.py) (English):
  * **Plot 1 (Attacker Profile)**: Menampilkan penurunan laju data Uplink RNTI 6 dari ~25 Mbps ke ~1.1 Mbps saat mitigasi aktif pada $t = 70,84$ detik.
  * **Plot 2 (Victim Profile)**: Menunjukkan laju data Downlink UE Normal (RNTI 5) yang mencapai kecepatan puncak ~114.87 Mbps secara bebas hambatan (tidak terpengaruh oleh mitigasi penyerang).
  * **Plot 3 (Attacker Restore - NEW)**: Menampilkan pemulihan laju data Uplink RNTI 6 kembali ke ~10.1 Mbps setelah perintah `RESTORE` dikirim pada $t = 143,93$ detik.
* **Hasil**: Seluruh file plot berhasil digenerasi dan disimpan di direktori `eval_figures/per_ue_v4/` dan `eval_figures/per_ue_v7/`.

---

## Hasil Visualisasi Evaluasi (English)

Untuk referensi naskah, berikut adalah visualisasi hasil evaluasi mitigasi per-UE yang baru digenerasi:

````carousel
![Attacker Throughput Profile](/home/telmat/.gemini/antigravity-ide/brain/1b795e67-0020-420f-94a3-e1eb12cc6c81/eval_attacker_throughput_en.png)
<!-- slide -->
![Victim Throughput Profile](/home/telmat/.gemini/antigravity-ide/brain/1b795e67-0020-420f-94a3-e1eb12cc6c81/eval_victim_throughput_en.png)
<!-- slide -->
![Attacker Restore Profile](/home/telmat/.gemini/antigravity-ide/brain/1b795e67-0020-420f-94a3-e1eb12cc6c81/eval_attacker_restore_en.png)
````



