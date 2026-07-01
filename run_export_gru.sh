#!/bin/bash
./venv/bin/python3 export_onnx_ue.py --arch gru --model models/gru_ue_v5.pt --scaler models/gru_ue_v5_scaler.pkl --out models/gru_ue_v5.onnx
