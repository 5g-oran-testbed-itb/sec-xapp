# Repo Cleanup & 3-Node Restructure — Design

**Status:** Approved (struktur), menunggu implementasi
**Date:** 2026-08-08
**Topic:** Merapikan `sec-xapp` sebagai lampiran TA yang reproducible: buang sampah, pisahkan kode milik sendiri dari vendor code, dokumentasikan testbed 3-node (RAN/RIC/Core), tulis README menyeluruh.

---

## 1. Konteks & tujuan

Repo ini akan dipakai sebagai **lampiran TA / reproducibility artifact** — audiensnya penguji atau peneliti lain yang ingin membaca kode dan mereproduksi hasil training + evaluasi. Ini bukan repo portofolio publik, bukan juga arsip pribadi asal rapi — jadi prioritasnya kejelasan metode dan jejak hasil, bukan kemudahan deploy sekali-klik.

**Temuan yang mendorong desain ini:**

- Working tree 6.9 GB, tapi hanya 530 file ter-*track*. Sisanya `venv/` (5.2 GB), `csv/` (402 MB), `flexric/` (901 MB, checkout yang salah/tidak dipakai).
- `flexric/` di root repo adalah gitlink rusak (mode 160000, tanpa `.gitmodules`) — checkout ini bahkan tidak punya `xapp_sec_moni.c`. Checkout yang benar-benar dipakai ada di `~/flexric` (di luar repo), dengan commit sendiri + 822 baris perubahan belum ter-*commit* di 17 berkas (xApp C + core FlexRIC: `near_ric.c`, `msg_handler_iapp.c`, `kpm_dec_asn.c`, dll).
- Node RAN memakai `srsRAN_Project` (di `~/TA-Rizqi-Nabiel/O-RAN-Testbed-Automation/Next_Generation_Node_B/`) dengan **3 berkas C++ dipatch**: `du_ue_manager.cpp` (F1AP UE ID translator), `e2sm_kpm_du_meas_provider_impl.cpp` (iterasi UE lengkap), `e2sm_kpm_report_service_impl.cpp` (bounds-checking anti-segfault untuk KPM style 4/5).
- Node Core memakai `~/core/docker_open5gs`, clone **bersih** dari fork upstream `herlesupreeth/docker_open5gs` @ commit `6531237` — tidak ada modifikasi lokal. Yang benar-benar milik pengguna hanyalah `~/core/config/*.yaml`.
- `.gitignore` RAN (`Next_Generation_Node_B/.gitignore`) meng-*ignore* `/configs/` — padahal folder itu berisi `cots_n78_copied.yml`/`gnb.yaml` yang jadi rujukan berulang di BAB3 sebagai konfigurasi aktual eksperimen.
- Kredensial SSH hardcoded (`CORE_PASS="123"`, `sshpass -p "123"`) di `patch_core.sh` dan `sync_gnb_config.sh` — **terverifikasi valid** (berhasil connect ke `10.91.2.4` saat investigasi).
- `run.sh` RAN memanggil `configs/gnb.yaml`, sementara dokumentasi BAB3 selalu merujuk `cots_n78_copied.yml` — perlu diverifikasi apakah `gnb.yaml` meng-*include* file tsb (dicek saat menulis README `deploy/ran/`, bukan pemblokir struktur).

**Non-tujuan:** mengkontainerkan RAN/RIC/Core (butuh USRP fisik + UE COTS + `ogstun`, tidak masuk akal dikontainerkan), memindahkan attack-orchestration scripts (`~/xapp/security-scripts/`, milik node controller/laptop terpisah, di luar cakupan pekerjaan ini), mengubah hasil eksperimen atau kode model.

---

## 2. Prinsip struktur

Tiga node = tiga "vendor codebase besar + tambalan/config kecil milik sendiri". Perlakuan konsisten berdasarkan **apakah vendor code-nya dipatch**:

| Node | Vendor codebase | Dipatch? | Perlakuan |
|---|---|---|---|
| RIC | FlexRIC (EURECOM, GitLab) | Ya — 822 baris/17 berkas | Fork publik + **submodule**, pin ke commit patch |
| RAN | srsRAN_Project | Ya — 3 berkas (DU manager, E2SM-KPM) | Fork publik + **submodule**, pin ke commit patch |
| Core | docker_open5gs (herlesupreeth) | Tidak | Cukup **catat commit upstream** (`6531237`) + config YAML sendiri |

Alasan submodule (bukan patch-series) untuk RIC & RAN: salah satu tambalan srsRAN adalah *bug fix* anti-*segfault* yang layak dilaporkan balik ke upstream — riwayat commit yang utuh jauh lebih berguna untuk itu daripada berkas `.patch` mentah. Submodule juga menjaga batas lisensi (FlexRIC = MPL-2.0, srsRAN = AGPL/proprietary dual-license) tetap jelas: kode vendor tetap di repo turunan vendor, bukan tercampur ke repo TA.

---

## 3. Struktur direktori target

```
sec-xapp/
├── README.md                      ← lihat §5, dokumentasi utama
├── src/detection/                 ← tidak berubah (model, scoring, feature schema)
├── scripts/
│   ├── train/                     ← train_gru_ue.py, train_lstm_ue.py, train_gru.py, train_lstm.py
│   ├── eval/                      ← evaluate_*.py, calibrate_threshold_*.py, verify_scoring_math.py
│   └── plot/                      ← plot_*.py, aggregate_grouped_ablation.py
├── models/                        ← checkpoint final + threshold + scaler saja (bukan seluruh v13-v24)
├── deploy/
│   ├── ric/
│   │   ├── README.md              ← cara build vendor/flexric, jalankan xApp, toggle mitigasi
│   │   ├── start_xapp_c.sh, start_xapp_c_mitigate.sh, start_xapp_automated.sh, start_bg.sh, stop_xapp.sh
│   │   └── my_xapp_kpm.conf, my_xapp_mitigate.conf
│   ├── ran/
│   │   ├── README.md              ← relasi gnb.yaml vs cots_n78_copied.yml, cara sync
│   │   ├── configs/               ← cots_n78_copied.yml, gnb_usrp.yaml (disalin dari node RAN)
│   │   └── sync_gnb_config.sh
│   └── core/
│       ├── README.md              ← cara apply config, link ke UPSTREAM_COMMIT
│       ├── UPSTREAM_COMMIT.txt    ← herlesupreeth/docker_open5gs @ 6531237
│       ├── config/                ← amf.yaml, smf.yaml, upf.yaml, dll (disalin dari ~/core/config)
│       └── patch_core.sh, change_subscriber_slice.sh   (kredensial dibersihkan — lihat §6)
├── observability/                 ← docker-compose.yml, grafana/, prometheus/, exporter/, testing_app/
├── docs/                          ← BAB1-5, hasil eksperimen, ablation reports (tetap)
└── vendor/
    ├── flexric/                   ← SUBMODULE → fork publik Anda, pin commit patch
    └── srsran/                    ← SUBMODULE → fork publik Anda, pin commit patch
```

**Yang dihapus dari repo** (bukan dipindah): file gitlink `flexric/` lama yang rusak, `venv/`, screenshot pribadi (`Screenshot 2026-*.png`, `Critical.png`, `Warning.png` — kecuali dipakai aktif sebagai figur di `docs/`), PDF kuliah (`Anomaly_Detection_for_...pdf` — cek dulu apakah ini referensi yang harus tetap ada), `__pycache__/`, `.pytest_cache/`, `grafana_session=`, `pidstat_overhead.log~`, `test.csv`, checkpoint `security_model_v13..v24.onnx(.data)` (24 versi lama — simpan hanya versi final yang direferensikan `docs/`).

---

## 4. Penanganan data & model

- **Model:** hanya checkpoint final yang dirujuk BAB4 masuk ke `models/` (GRU v5, LSTM v6 + scaler + threshold JSON). Versi eksperimen (`ablation_loss/`, dsb.) boleh tetap ada kalau kecil dan dirujuk laporan ablation; kalau tidak, dibuang.
- **Dataset CSV** (402 MB, saat ini di-*ignore* via `*.csv`): **tidak** masuk repo. Disediakan manifes SHA256 + deskripsi (baris, durasi, skenario) di `docs/`, dataset dirilis terpisah (link diisi user nanti — Google Drive/Zenodo) dan diunduh via skrip kecil `scripts/fetch_dataset.sh` (unduh + verifikasi checksum).

---

## 5. Isi README.md (root)

README wajib berfungsi sebagai **peta navigasi + dokumentasi kode + panduan deployment**, terstruktur:

1. **Ringkasan proyek** — 1 paragraf: deteksi anomali xApp Near-RT RIC berbasis LSTM/GRU-Autoencoder, testbed O-RAN 3-node fisik.
2. **Topologi testbed** — tabel node/IP/software/lokasi direktori (versi bersih dari `docs/CLAUDE.md` §Topologi), diagram alur E2AP/E42/N3/ogstun.
3. **Struktur repo** — tabel folder → isi → fungsi (mengacu §3 di atas), termasuk penjelasan kenapa `vendor/` berupa submodule.
4. **Dokumentasi kode** — per modul di `src/detection/`: fungsi `feature_schema.py`, `scoring.py`, `gru_autoencoder.py`/`lstm_autoencoder.py`, `detector.py`, dengan tautan ke docstring/BAB3 yang relevan; tabel 10 fitur (dari `docs/CLAUDE.md`).
5. **Cara reproduksi hasil** — urutan: fetch dataset → `scripts/train/` → `scripts/eval/` → `scripts/plot/`, dengan command persis dan output yang diharapkan (mengambil pola dari `docs/CLAUDE.md` §LSTM-Autoencoder pipeline).
6. **Cara deployment testbed** — per node (RIC/RAN/Core), merujuk `deploy/<node>/README.md` masing-masing untuk detail, tapi root README memuat ringkasan: clone `--recursive`, build FlexRIC+xApp, build srsRAN, jalankan Open5GS, urutan startup (RIC → RAN → Core atau sesuai kebutuhan E2AP handshake).
7. **Skenario serangan & mitigasi** — tabel skenario (Baseline/UL Flood/DL Flood/Burst/RRC Storm/RF Jammer) dan strategi mitigasi per skenario (dari `docs/CLAUDE.md` §Skenario & §Mitigasi), dengan catatan bahwa orkestrasi serangan ada di repo controller terpisah (disebut, tidak disalin).
8. **Known issues / keterbatasan** — ringkas dari `docs/CLAUDE.md` §Known Issues (DRB metrics selalu 0, CQI keep-last, dst.) supaya pembaca tidak salah interpretasi hasil.
9. **Lisensi & atribusi** — linkkan lisensi FlexRIC (MPL-2.0) dan srsRAN, jelaskan submodule bukan bagian dari lisensi kode Anda sendiri.

`deploy/ric/README.md`, `deploy/ran/README.md`, `deploy/core/README.md` masing-masing berisi detail teknis spesifik node (build command, config yang perlu disesuaikan per environment, known quirks) — root README hanya me-*link* ke situ.

---

## 6. Keamanan (wajib sebelum publish)

- `patch_core.sh` dan `sync_gnb_config.sh`: ganti `sshpass -p "123"` dengan SSH key + variabel environment (`CORE_HOST`, `CORE_USER` tetap boleh hardcode karena itu bukan rahasia, tapi password tidak boleh literal di skrip).
- Audit ulang seluruh `deploy/` dan `docs/` untuk string password/token lain sebelum commit pertama ke fork publik.
- Fork FlexRIC & srsRAN yang di-push publik: pastikan tidak ada log/`.log`, `compile_commands.json`, atau file `(Copy).c`/`.backup` ikut ter-*commit* — bersihkan working tree `~/flexric` dan checkout RAN dulu sebelum push.

---

## 7. `.gitignore` (target, gabungan repo utama + catatan untuk repo node)

Repo utama `sec-xapp` (setelah restrukturisasi):
```
venv/
env/
*.csv
*.log
!deploy/**/*.log.example
__pycache__/
*.pyc
.pytest_cache/
models/ablation_loss/**/*.pt        # kecuali yang direferensikan laporan — putuskan saat migrasi
```

Repo `Next_Generation_Node_B` (di node RAN, **di luar** `sec-xapp` — perbaikan disarankan, dieksekusi manual oleh user di node RAN):
```diff
-/configs/
 /czmq/
 /libzmq/
 /logs/
 /o1-adapter/
 /srsRAN_Project/
 czmq
 install_time.txt
 libzmq
 !install_patch_files/
+configs/*.bak.*
 *.log
```

---

## 8. Verifikasi yang masih terbuka (diselesaikan saat implementasi, bukan pemblokir desain)

1. Apakah `configs/gnb.yaml` (dipanggil `run.sh`) meng-*include* `cots_n78_copied.yml`, atau keduanya berkas independen? Perlu dicek langsung isi `gnb.yaml` di node RAN.
2. PDF kuliah di root (`Anomaly_Detection_for_Mitigating_xApp_and_E2_Interface_Threats...pdf`, 4.3 MB) — referensi yang harus tetap ada di `docs/`, atau berkas unduhan pribadi yang boleh dibuang?
3. Cakupan final `models/ablation_loss/` dan `results/` — mana yang dirujuk laporan (harus tetap) vs. eksperimen mentah (boleh diarsipkan di luar repo).

---

## 9. Definisi selesai

- `git clone --recursive` menghasilkan working tree < 300 MB (tanpa dataset), semua submodule ter-resolve.
- README root menjawab: apa proyek ini, bagaimana strukturnya, bagaimana mereproduksi hasil, bagaimana deploy ke 3 node, di mana batasan yang perlu diketahui pembaca — tanpa perlu bertanya ke penulis.
- Tidak ada kredensial literal di berkas manapun dalam repo.
- Tidak ada file gitlink rusak atau submodule tanpa `.gitmodules`.
