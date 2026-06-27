#!/usr/bin/env python3
"""
Precompute per-row GRU Reconstruction Error, IDS stage, and alert type for a
labeled per-UE dataset, so a host-side streamer can replay it into the live
pipeline without onnxruntime. Runs INSIDE the xapp-testing container (which has
onnxruntime + the v4 models). Prints one CSV line per source row to stdout:

    mse,stage,alert_type

aligned to the source file's row order (GRU-hybrid decision).
"""
import os
import sys

sys.path.insert(0, "/app")
os.environ.setdefault("CSV_DIR", "/data/csv")
os.environ.setdefault("UE_ONNX_MODEL", "/data/models/gru_ue_v4.onnx")
os.environ.setdefault("LSTM_UE_ONNX_MODEL", "/data/models/lstm_ue_v4.onnx")

import numpy as np  # noqa: E402
import testing_app as ta  # noqa: E402

LABELMAP = {1: "ul_flood", 2: "dl_flood", 3: "burst", 4: "roq"}
STAGE2_CONSEC = 5  # consecutive Stage-1 alerts to escalate to Stage-2 (critical)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/data/csv/dataset_attack_ue_juni.csv"
    rows = ta.load_rows(path)

    d = ta._detect_rows(rows, "gru")          # rule + GRU per RNTI
    rule_sev  = d["rule_sev"]
    gru_sev   = d["ml_sev"]
    gru_score = d["ml_scores"]
    final = np.maximum(rule_sev, gru_sev)     # GRU-hybrid

    n = len(rows)
    stage  = np.zeros(n, dtype=int)
    atypes = ["none"] * n
    # Stage escalation + alert-type latch, per RNTI on that UE's own timeline.
    # alert_type stays consistent with stage: whenever stage>0 we show the most
    # recent attack class seen (latched), so the table's Alert and Stage columns
    # never contradict (no "NORMAL" alert while Stage shows Warning).
    for rnti, idxs in d["rnti_groups"].items():
        consec = 0
        last_type = "none"
        for i in idxs:
            lbl = int(rows[i]["label"])
            if lbl > 0:
                last_type = LABELMAP.get(lbl, "none")
            if final[i] >= 1:
                consec += 1
                stage[i] = 2 if consec >= STAGE2_CONSEC else 1
                atypes[i] = last_type if last_type != "none" else "none"
            else:
                consec = 0
                stage[i] = 0
                atypes[i] = "none"
                last_type = "none"

    out = [f"{gru_score[i]:.6f},{stage[i]},{atypes[i]}" for i in range(n)]
    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
