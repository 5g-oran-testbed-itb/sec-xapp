# Analisis dan Perbaikan Kegagalan E2SM-RC Mitigasi (xApp)

> Diperbarui: 2026-07-02 21:11 — setelah test slicing + nc (moni di-kill). Kontrol
> DI-ACK tapi PRB TETAP tidak turun → masalah terisolasi ke **enforcement srsRAN**,
> bukan xApp. Lihat §4 & §9 (Rencana Perbaikan).

## Status Keseluruhan

| Komponen | Status |
|---|---|
| E2AP Encoding (APER) | ✅ OK — tidak ada lagi `Failed the encoding in type value` |
| RIC–gNB koneksi | ✅ Terhubung — `NearRT_RIC_IP = "10.91.2.2"` |
| E2SM-RC Control diterima gNB | ✅ **Struktur B** di-ACK (`CONTROL ACKNOWLEDGE rx`) |
| Struktur RAN Param yang benar | ✅ **Terkunci = Struktur B** (lihat §2) |
| IPC server melayani `nc` manual | ⚠️ Single-client — harus kill moni dulu (§6b); fix opsional |
| Slicing block ditambahkan di gNB | ✅ Applied (lokal + remote, backup) |
| Mitigasi PRB throttle **efektif** (brate turun) | ❌ **Masih gagal walau ACK diterima** — enforcement srsRAN (§4) |

---

## 1. Deskripsi Masalah Awal

E2SM-RC Control Request dari `xapp_sec_mitigate` awalnya membuat RIC crash di layer
E2AP APER encoding (`Failed the encoding in type value and xml_type = value`).
Masalah encoding ini **sudah teratasi** — kontrol sekarang ter-encode & terkirim.

---

## 2. Struktur RAN Parameter — Struktur B (TERKUNCI ✅)

**Fungsi**: `build_ctrl_msg()` di `xapp_sec_mitigate.c`.

Ada dua kandidat nesting untuk parameter PRB (Min=ID11, Max=ID12, Ded=ID13):

**Struktur A** — ID11/12/13 **di dalam** ID3 (dipakai `xapp_prb_ctrl.c` & git HEAD):
```text
RRM Policy Ratio Group (id=2)
  └─ RRM Policy (id=3)
       ├─ RRM Policy Member List (id=5)
       ├─ Min/Max/Ded PRB (id=11/12/13)
```

**Struktur B** — ID11/12/13 **sejajar** ID3, di bawah ID2 (dipakai sekarang):
```text
RRM Policy Ratio Group (id=2)
  ├─ RRM Policy (id=3)
  │    └─ RRM Policy Member List (id=5)   ← HANYA ini
  ├─ Min PRB Policy Ratio (id=11)
  ├─ Max PRB Policy Ratio (id=12)
  └─ Dedicated PRB Policy Ratio (id=13)
```

### Bukti EMPIRIS — kenapa B, bukan A

| Struktur | Hasil di testbed ini | Sesi |
|---|---|---|
| **B** | gNB balas `CONTROL ACKNOWLEDGE rx` — diterima & di-decode | 20:05 |
| **A** | gNB **tidak** balas ACK → RIC: `Pending event timeout... Communication with E2 Node lost?` → pane RIC freeze | 20:37 |

**Kesimpulan:** srsRAN yang ter-deploy **hanya menerima Struktur B**. Struktur A
memutus koneksi E2.

> ⚠️ **Pelajaran (jangan terulang):** `xapp_prb_ctrl.c` memberi komentar "proven-working"
> dengan Struktur A, tapi itu untuk **build srsRAN + flexric yang BERBEDA** — dibuktikan
> oleh perbedaan nama field (`octet_str` vs `octet_str_ran`). Komentar "proven-working"
> dari referengsi lintas-versi **tidak boleh** dijadikan dasar tanpa verifikasi empiris
> di testbed sendiri. Pada 20:34 struktur sempat di-revert ke A berdasarkan komentar itu,
> menyebabkan E2 lost pada 20:37; sudah dikembalikan ke B (build 20:46).

**Kode terkunci (`build_ctrl_msg`)**:
```c
seq_ran_param_t p3 = make_struct_param(3, &p5, 1);          /* ID3 = {ID5} saja */
seq_ran_param_t p2_ch[4] = {p3, p11, p12, p13};             /* ID11/12/13 sejajar ID3 */
seq_ran_param_t p2 = make_struct_param(2, p2_ch, 4);
```

---

## 3. Root Cause: RIC IP Config (RESOLVED ✅)

`ric.conf` semula `NearRT_RIC_IP = "127.0.0.1"` → gNB remote tidak bisa konek.
Fix: `sudo sed -i 's/127.0.0.1/10.91.2.2/' /usr/local/etc/flexric/ric.conf`.

---

## 4. Root Cause AKTIF: srsRAN meng-ACK RC control tapi TIDAK mengenforce (❌)

**Hasil test 21:11** (moni di-kill agar `nc` bisa jalan — lihat §6b):

```
[NEAR-RIC]: CONTROL SERVICE sent
[iApp]: E42_RIC_CONTROL_REQUEST rx
[NEAR-RIC]: CONTROL ACKNOWLEDGE rx     ← gNB ACK
[iApp]: RIC_CONTROL_ACKNOWLEDGE tx
```
→ **Kontrol terkirim & di-ACK, tapi PRB/brate UE aktif TIDAK turun** — bahkan setelah
blok `slicing:` (sst=1) ditambahkan ke `cell_cfg`.

**Kesimpulan yang menyempit:** masalah **bukan** di xApp, bukan di struktur pesan, bukan
di IPC. srsRAN Project yang ter-deploy **menerima & meng-ACK** E2SM-RC Style2/Action6,
tetapi **tidak menerapkan** RRM Policy Ratio itu ke scheduler pada saat runtime.
Dua kemungkinan tersisa (dibedakan oleh test statis di §9):

- **(a) Slice tidak benar-benar aktif/terikat** — gNB mungkin belum restart dengan
  blok slicing, atau syntax slicing ditolak srsRAN, atau UE tidak ter-map ke slice sst=1.
- **(b) srsRAN tidak mengimplementasikan enforcement RC-driven** — E2SM-RC control
  action untuk RRM policy hanya diterima/di-ACK tapi handler-nya no-op di scheduler
  (keterbatasan build srsRAN). Ini umum di srsRAN Project.

**Catatan penting:** `cots_n78_copied.yml` punya `remote_control: {enabled: true, port: 5555}`
— srsRAN punya jalur remote-control terpisah dari E2. Bisa jadi alternatif enforcement
(lihat §9 opsi C).

---

## 5. Insiden Freeze Pane RIC (20:37) — dijelaskan

- **Gejala:** setelah `nc` throttle, pane RIC mencetak dump `E2AP-PDU` lalu
  `Pending event timeout happened. Communication with E2 Node lost?` dan freeze.
- **Penyebab:** Struktur A (revert 20:34) tidak bisa di-decode srsRAN → tidak ada ACK →
  RIC menganggap node hilang. Metrics gNB tetap update (MAC hidup) — jadi yang putus
  hanya jalur E2/decode, bukan gNB crash.
- **Catatan:** dump XML `RICsubscriptionRequest` dengan tag `ricEventTriggerDefinition`/
  `ricAction-ToBeSetup-List` yang terlihat "kosong" **bukan** bukti subscription rusak —
  isi E2SM ada di dalam OCTET STRING opak, jadi memang selalu tampil kosong di render
  XML level-E2AP. Itu FlexRIC mendump state **saat** node-lost, bukan penyebabnya.
- **Resolusi:** kembali ke Struktur B (build 20:46). **Konfirmasikan** freeze tidak
  muncul lagi saat run berikutnya. Kalau masih muncul dengan B → isu terpisah (mis. gNB
  E2 agent benar-benar crash) dan butuh `/tmp/gnb.log`.

---

## 6. Isolasi: Test Manual (tanpa menunggu deteksi ML)

Bypass pipeline deteksi agar enforcement E2SM-RC teruji sendiri. Pakai nilai
**non-zero kecil** (5%) dulu, bukan 0:

```bash
# saat speedtest DL berjalan (brate ~100M), throttle UE target (ganti ue_id sesuai FORMAT3)
echo '{"action":"THROTTLE","prb_limit":5,"ue_id":3}' | nc -U /tmp/sec_xapp_mitigate.sock
# pantau tabel gNB Pane 1: kolom brate rnti target HARUS turun dalam ~1-2s
echo '{"action":"RESTORE","prb_limit":100,"ue_id":3}' | nc -U /tmp/sec_xapp_mitigate.sock
```

Catatan: monitor otomatis mengirim `THROTTLE` dengan `prb_limit=0` (block). Jika 5%
jalan tapi 0% tidak → srsRAN memperlakukan max=0% sebagai "unset"; ganti nilai block
jadi 1–2%.

---

## 6b. Root Cause: `nc` manual hang — IPC server single-client (dijelaskan ✅)

**Gejala:** `echo ... | nc -U /tmp/sec_xapp_mitigate.sock` hang (perlu ^C), tak ada ACK.

**Root cause (terbukti dari kode):**
- `xapp_sec_moni` connect ke socket **sekali di startup** dan **menahan** koneksi itu
  selamanya (`g_ipc_fd` persistent — [xapp_sec_moni.c:887](copy-xapp/xapp_sec_moni.c#L887)).
- `xapp_sec_mitigate` pakai `listen(fd, 1)` lalu `accept()` **satu** klien dan blocking
  di `recv()` sampai klien itu putus ([xapp_sec_mitigate.c:452-460](copy-xapp/xapp_sec_mitigate.c#L452)).
- Jadi mitigate **permanen sibuk melayani moni** → koneksi `nc` menumpuk di backlog,
  tak pernah di-`accept()` → hang, tanpa ACK.

**Implikasi:** semua throttle yang benar-benar sampai gNB selama ini berasal dari
**jalur moni** (deteksi otomatis), BUKAN `nc`. `nc` hanya jalan kalau moni di-kill dulu.

**Workaround (dipakai di test 21:11):** `Ctrl+C` moni di Pane 3 → slot bebas → `nc` jalan.

**Fix permanen (opsional, §9 opsi D):** ubah IPC server jadi multi-client (multiplex
`select()` atas listen-fd + semua client-fd) supaya `nc` ops + moni auto bisa bersamaan.

---

## 7. Jalur Terpisah: Deteksi/ML tidak memicu `final_sev=2`

Independen dari enforcement. Pada sesi 20:04–20:09: `anomaly_score=0.000000` dan
`cqi=0.0` sepanjang sesi → LSTM cell-level tak menghasilkan skor. `stage2_confirmed=1`
di CSV hanya berarti "stage-1 bertahan >30s", **bukan** `final_sev==2`. Untuk demo
throttle end-to-end, gunakan **test manual (§6)** yang mem-bypass ML sepenuhnya, lalu
perbaiki pipeline ML terpisah (warmup window, sumber CQI dari KPM).

---

## 8. Status Langkah

| # | Langkah | Status |
|---|---------|--------|
| 1 | Fix E2AP encoding | ✅ |
| 2 | Kunci Struktur B (A putus E2, terbukti 20:37) | ✅ build 20:46 |
| 3 | Fix `ric.conf` IP | ✅ |
| 4 | Root cause `nc` hang = IPC single-client | ✅ terbukti (§6b) |
| 5 | Tambah blok `slicing` di `cell_cfg` gNB | ✅ applied (lokal+remote) |
| 6 | Test manual throttle 5% (moni di-kill) | ✅ dijalankan 21:11 |
| 7 | **Hasil: control di-ACK tapi PRB TIDAK turun** | ❌ enforcement srsRAN (§4) |
| 8 | Pipeline ML memicu `final_sev=2` | ❌ jalur terpisah (§7) |

---

## 9. RENCANA PERBAIKAN (prioritas saat lanjut nanti)

Yang SUDAH pasti benar: encoding ✅, struktur B ✅, IPC (via workaround) ✅, kontrol
sampai gNB & di-ACK ✅. **Satu-satunya yang tersisa = gNB tidak mengeksekusi throttle.**
Lakukan berurutan; berhenti begitu satu langkah menjelaskan/menyelesaikan.

### Langkah 1 — TEST PENENTU: slice ratio STATIS (tanpa xApp)
Tujuan: pisahkan "scheduler slice tidak enforce" vs "RC control tidak diterapkan".
Di config gNB, set **statis** `max_prb_policy_ratio: 5` (bukan lewat RC), restart gNB,
lalu speedtest:
```yaml
cell_cfg:
  slicing:
    - sst: 1
      sched_cfg:
        min_prb_policy_ratio: 0
        max_prb_policy_ratio: 5     # paksa statis
```
- **Throughput UE turun ke ~5%** → scheduler slice srsRAN **bisa** enforce. Berarti
  masalah spesifik di jalur RC-control→scheduler (lanjut Langkah 2). Balikin ke 100.
- **Throughput tetap penuh** → slice **tidak terikat** ke UE / tidak enforce sama sekali.
  Cek: apakah gNB benar restart dgn slicing? apakah `sd` perlu di-set? apakah UE PDU
  session ter-map ke sst=1? Cek log startup gNB untuk baris pembuatan slice.

### Langkah 2 — Cek dukungan enforcement RC di srsRAN
Kalau Langkah 1 = slice statis bisa enforce tapi RC tidak:
```bash
# di mesin gNB, cari handler control action RRM policy
grep -rniE "control_action|rrm_policy|prb_policy_ratio|e2sm_rc.*control|ric_control" \
  ~/TA-Rizqi-Nabiel/.../srsRAN_Project/lib/e2 | grep -iE "rc|policy|prb"
# cek versi
cd ~/TA-Rizqi-Nabiel/.../srsRAN_Project && git log -1 --oneline
```
Kalau handler RC untuk RRM policy **no-op / tidak ada** → keterbatasan build. Opsi:
upgrade/patch srsRAN ke versi yang mengimplementasikan, atau pindah ke Opsi C/D.

### Opsi C — Jalur mitigasi alternatif (kalau RC enforcement tak didukung)
`remote_control: {enabled: true, port: 5555}` sudah ON di gNB. Selidiki apakah remote
control srsRAN bisa set slice/PRB ratio runtime; kalau ya, mitigator kirim throttle
ke `127.0.0.1:5555` (di sisi gNB) alih-alih via E2SM-RC. (Perlu SSH port-forward /
mitigator jalan di sisi gNB.)

### Opsi D — Fix IPC multi-client (biar `nc` + moni bareng)
Ubah `xapp_sec_mitigate` `ipc_recv_loop`/main jadi `select()`-based multiplex atas
listen-fd + banyak client-fd. Kecil, single-thread, tak perlu lock. Hilangkan keharusan
kill moni untuk test manual. (Kualitas hidup, tidak menyelesaikan enforcement.)

### Opsi E — Fallback narasi TA (kalau srsRAN memang tak bisa enforce)
Closed-loop **signaling** sudah terbukti: deteksi → IPC → E2SM-RC control → **gNB ACK**.
Untuk demo enforcement, opsi: (1) tampilkan test slice statis (Langkah 1) sebagai bukti
kapabilitas throttle scheduler; (2) atau jalankan enforcement di RAN yang mendukung RC
PRB control (mis. OAI). Dokumentasikan batas platform srsRAN secara jujur.

### Jalur paralel (kapan pun) — perbaiki deteksi ML (§7)
`anomaly_score=0` & `cqi=0` → moni tak pernah `final_sev=2` → throttle otomatis tak
terpicu. Perlu: sumber CQI dari KPM, warmup window LSTM terisi sebelum inferensi.
Independen dari masalah enforcement di atas.
