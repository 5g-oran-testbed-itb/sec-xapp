#!/bin/bash
cd "$(dirname "$0")/../.." || exit 1
./venv/bin/python3 scripts/export/export_onnx_ue.py --arch gru --model models/gru_ue_v5.pt --scaler models/gru_ue_v5_scaler.pkl --out models/gru_ue_v5.onnx
