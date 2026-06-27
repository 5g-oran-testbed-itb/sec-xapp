#!/usr/bin/env python3
"""
Replay a labeled per-UE dataset into the LIVE pipeline so the Grafana
"Per-UE Live" dashboard fills up as if the xApp were running — no USRP needed.

Appends rows gradually to:
  csv/per_ue_training_SIM_<ts>.csv   (feature gauges: PRB, throughput, ...)
  csv/ue_alerts_SIM_<ts>.csv         (alert gauges: mse, stage, alert_type)

The csv_exporter container tails the newest of each pattern, exposes the
xapp_ue_* metrics, Prometheus scrapes them, Grafana shows them.

Usage:
  python3 tools/sim_stream.py --precomputed /tmp/sim_pre.csv \
      --source csv/dataset_attack_ue_juni.csv \
      --offset 0 --count 1200 --speed 0.25 [--loop]
"""
import argparse
import csv
import os
import time
from datetime import datetime

GRU_THRESH = 0.025969
ALERT_HEADER = ["timestamp_ms", "rnti", "rule_mask", "rule_stage",
                "mse", "threshold", "alert_type"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="csv/dataset_attack_ue_juni.csv")
    ap.add_argument("--precomputed", required=True,
                    help="CSV from sim_precompute.py: mse,stage,alert_type per row")
    ap.add_argument("--csv-dir", default="csv")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--count", type=int, default=1200)
    ap.add_argument("--speed", type=float, default=0.25, help="seconds per row")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--rnti-remap", default="",
                    help="remap rnti values, e.g. '7=4601,8=4602' (realistic C-RNTI)")
    args = ap.parse_args()

    remap = {}
    if args.rnti_remap:
        for pair in args.rnti_remap.split(","):
            k, v = pair.split("=")
            remap[k.strip()] = v.strip()

    with open(args.source, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        src = list(reader)
    with open(args.precomputed, newline="") as f:
        pre = [line.rstrip("\n").split(",") for line in f if line.strip()]

    if len(pre) != len(src):
        print(f"WARN: precomputed rows ({len(pre)}) != source rows ({len(src)})")
    n = min(len(src), len(pre))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    feat_path  = os.path.join(args.csv_dir, f"per_ue_training_SIM_{ts}.csv")
    alert_path = os.path.join(args.csv_dir, f"ue_alerts_SIM_{ts}.csv")

    ffeat  = open(feat_path, "w", newline="")
    falert = open(alert_path, "w", newline="")
    fw = csv.writer(ffeat);  fw.writerow(header)
    aw = csv.writer(falert); aw.writerow(ALERT_HEADER)
    ffeat.flush(); falert.flush()

    print(f"[sim] feature → {feat_path}")
    print(f"[sim] alerts  → {alert_path}")
    print(f"[sim] streaming rows {args.offset}..{args.offset + args.count} "
          f"@ {args.speed}s/row  (loop={args.loop})")

    rnti_i  = header.index("rnti")
    ts_i    = header.index("timestamp_ms")

    while True:
        end = min(args.offset + args.count, n)
        for i in range(args.offset, end):
            row = list(src[i])
            if row[rnti_i] in remap:
                row[rnti_i] = remap[row[rnti_i]]
            mse, stage, atype = pre[i]
            fw.writerow(row); ffeat.flush()
            aw.writerow([row[ts_i], row[rnti_i], 0, stage,
                         mse, f"{GRU_THRESH:.6f}", atype]); falert.flush()
            time.sleep(args.speed)
        if not args.loop:
            break
        print("[sim] loop restart")

    ffeat.close(); falert.close()
    print("[sim] done")


if __name__ == "__main__":
    main()
