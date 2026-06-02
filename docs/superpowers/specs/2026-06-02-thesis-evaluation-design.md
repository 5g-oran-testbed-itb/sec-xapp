# Thesis Evaluation Design — Paper-Grade

**Tanggal:** 2026-06-02
**Status:** Approved

---

## Goal

Menghasilkan evaluasi paper-grade untuk thesis S1 pada sistem deteksi dan mitigasi anomali 5G RAN berbasis O-RAN Near-RT xApp. Evaluasi mencakup tiga lapisan: (1) deteksi known attacks, (2) generalisasi ke unknown attack, dan (3) efektivitas mitigasi. GRU ensemble dievaluasi sebagai studi perbandingan arsitektur untuk thesis partner.

---

## Konteks Sistem

**Kontribusi utama (penulis):** LSTM dual ensemble (v16+v22) + rule-based hybrid + E2SM-RC/iptables mitigation

**Kontribusi partner:** GRU dual ensemble (A+B) sebagai perbandingan arsitektur

**Dataset existing:** `csv/dataset_attack_mei.csv` — 17,941 baris, 4 attack types (label 1–4), collected detection-only

**Baseline performance (hybrid, Stage1+):**
- UL Flood: 99.2%, DL Flood: 99.8%, Burst: 99.7%, RRC Storm: 94.0%
- FPR Stage2: 1.37%

---

## Section 1: Detection Evaluation

### 1a. Known Attacks (existing dataset)

Dataset: `csv/dataset_attack_mei.csv`

Evaluasi 3-way untuk setiap attack type:

| Komponen | Script |
|----------|--------|
| Rule-based only | `evaluate_detection.py --mode rule` |
| LSTM only | `evaluate_detection.py --mode lstm` |
| Hybrid (Rule + LSTM) | `evaluate_detection.py --mode hybrid` |
| GRU ensemble (partner) | `evaluate_gru.py` |

Metrics per attack: recall, precision, F1, FPR, Stage1 latency, Stage2 confirmation rate.

### 1b. Unknown Attack — Multi-vector Low-and-Slow (Option B)

**Nama skenario:** Unknown / Low-and-Slow Multi-vector Attack
**Label:** 7
**Durasi:** 8–10 menit per sesi
**Mode xApp saat pengambilan data:** detection-only
**Minimal sesi:** 2 (untuk reproducibility)

**Timeline per sesi:**

| Waktu | UE-1 | UE-2 |
|-------|-------|-------|
| 0–2 menit | benign browsing/idle | benign |
| 2–5 menit | UL low-rate iperf + reconnect periodik | benign |
| 5–6 menit | recovery benign | benign |
| 6–9 menit | benign | UL low-rate iperf + reconnect periodik |
| 9–10 menit | recovery benign | recovery benign |

**Parameter iperf:**
```bash
iperf3 -u -b 30M -t 180
```
Kalibrasi target: `prb_usage_ul_ratio` ≈ 0.40–0.65 (di bawah rule UL flood threshold).

**Parameter reconnect:**
- Airplane/mobile-data toggle tiap 30–60 detik
- Tidak kurang dari 30 detik (interval lebih pendek = RRC Storm biasa)

**Expected behavior:**

| Komponen | Ekspektasi |
|----------|------------|
| Rule UL Flood | tidak trigger — PRB/UL di bawah threshold |
| Rule RRC Storm | tidak trigger — reconnect terlalu jarang |
| Rule PRB Overload | tidak trigger |
| LSTM | reconstruction error naik — kombinasi moderate UL + periodic RACH tidak ada di training data |
| UE benign | tetap normal, tidak terdeteksi |

**Argumen thesis:** Rule-based gagal karena tiap indikator di bawah threshold individual. LSTM menangkap kombinasi temporal yang tidak natural — tidak bisa dikritik dengan "tinggal turunkan threshold" karena bukan satu threshold yang gagal, melainkan pola multi-fitur temporal.

**Switch label:**
```bash
./helpers/switch_label.sh 7 unknown_low_slow UE1
```

---

## Section 2: Mitigation Evaluation

**Setup:** `./start_xapp_c_mitigate.sh` — Layer 2 iptables aktif via STAGE2-CRITICAL trigger.

**Attack target untuk eksperimen:** UL Flood dan DL Flood (paling measurable via iperf throughput).

### Metrik

| Metrik | Cara Ukur |
|--------|-----------|
| **Throughput reduction** | iperf client di UE: Mbps sebelum vs sesudah iptables aktif |
| **Time-to-mitigate** | Stage2 latency (5s confirmation) + SSH apply latency (~1–2s) = ~6–7s total |
| **Mitigation trigger rate** | % attack rows yang memicu `stage2_confirmed` per attack type |
| **False Mitigation Rate (FMR)** | % benign rows yang memicu stage2 → iptables wrongly applied |
| **Throughput restoration** | Manual saat ini — dicatat sebagai limitasi |

### Prosedur eksperimen

1. Jalankan `./start_xapp_c_mitigate.sh`
2. Di UE: jalankan iperf server `iperf3 -s`
3. Di attacker: jalankan iperf client, catat throughput baseline
4. Trigger attack, catat throughput saat mitigation aktif (ekspektasi: drop ke ~0)
5. Stop attack, catat restoration (manual script restart)

### Unknown attack + mitigation

Setelah sesi detection-only selesai, ulangi satu sesi unknown attack (label 7) dalam mitigation mode. Tujuan: membuktikan sistem merespons serangan yang belum pernah dilihat sebelumnya — ini claim terkuat di thesis.

### Limitasi yang perlu didokumentasikan

- Restoration bersifat manual (script restart), bukan automatic cooldown
- Layer 2 iptables memblokir seluruh UE subnet (blunt instrument, bukan per-UE throttle)
- E2SM-RC PRB throttle (Layer 1) dinonaktifkan karena E2 timeout issue di srsRAN

---

## Section 3: Architectural Comparison — LSTM vs GRU (Partner)

**Framing pertanyaan riset:** *"Apakah GRU ensemble dengan window lebih panjang (seq_len=30) memberikan peningkatan deteksi RRC Storm dibanding LSTM ensemble (seq_len=10), dengan trade-off parameter yang lebih sedikit?"*

**Dataset:** `csv/dataset_attack_mei.csv` + dataset unknown attack label 7 (apple-to-apple comparison).

| Dimensi | LSTM v16+v22 | GRU-A (seq_len=10) + GRU-B (seq_len=30) |
|---------|-------------|------------------------------------------|
| Recall per attack (4 known) | existing | `evaluate_gru.py` |
| RRC Storm recall | 94.0% (hybrid) | TBD |
| Unknown attack recall | TBD | TBD |
| Parameter count | baseline | ~75% dari LSTM |
| Inference latency | baseline | TBD |
| FPR Stage1 | 7.56% | TBD |

**Skrip evaluasi:** `evaluate_gru.py --compare-lstm` — output format identik dengan `evaluate_detection.py` untuk langsung dibandingkan.

---

## Checklist Eksperimen yang Perlu Dilakukan

- [ ] Kalibrasi iperf parameter untuk unknown attack (verifikasi `prb_usage_ul_ratio` ≈ 0.40–0.65)
- [ ] Sesi 1: Unknown attack detection-only, UE-1 sebagai attacker
- [ ] Sesi 2: Unknown attack detection-only, UE-2 sebagai attacker (role-swap)
- [ ] Sesi 3: Mitigation experiment — UL Flood dengan iptables aktif, catat throughput
- [ ] Sesi 4: Mitigation experiment — Unknown attack (label 7) dengan mitigation mode
- [ ] GRU-B training selesai → jalankan `evaluate_gru.py` → bandingkan dengan LSTM
- [ ] Tulis tabel perbandingan final semua komponen

---

## Output Akhir yang Diharapkan

```
results/
  eval_results_attack_mei_rule3c.json       ← existing (known attacks, hybrid)
  eval_results_unknown_attack_s1.json       ← sesi 1 unknown attack
  eval_results_unknown_attack_s2.json       ← sesi 2 unknown attack
  eval_results_mitigation_ul_flood.json     ← mitigation experiment
  eval_results_mitigation_unknown.json      ← mitigation + unknown attack
  eval_results_gru_ensemble_v1.json         ← GRU comparison (partner)
```
