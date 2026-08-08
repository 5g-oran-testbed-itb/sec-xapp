"""
Patch rolling stats columns yang bernilai nol di CSV dataset baru.

Root cause: run_cell_inference() punya guard `if (session == NULL) return;`
sehingga rolling stats tidak pernah dihitung saat ONNX session NULL.

Formula yang diverifikasi cocok dengan C binary (untuk data tanpa session gap):
  prb_dl_roll_mean    = rolling(10, min_periods=1).mean()
  prb_dl_roll_std     = rolling(10, min_periods=1).std(ddof=0)
  prb_ul_roll_std     = rolling(10, min_periods=1).std(ddof=0)
  prb_ul_roll_max     = rolling(10, min_periods=1).max()
  prb_ul_roll_max_100 = rolling(100, min_periods=1).max()
"""

import pandas as pd
import numpy as np
import os

FILES = [
    "csv/dataset_training_mei.csv",
    "csv/dataset_training_mei_reconnect.csv",
    "csv/dataset_validation_mei.csv",
]

ROLLING_COLS = [
    "prb_dl_roll_mean",
    "prb_dl_roll_std",
    "prb_ul_roll_std",
    "prb_ul_roll_max",
    "prb_ul_roll_max_100",
]


def compute_rolling_stats(df: pd.DataFrame) -> pd.DataFrame:
    dl = df["prb_usage_dl_ratio"].astype(float)
    ul = df["prb_usage_ul_ratio"].astype(float)

    df["prb_dl_roll_mean"]    = dl.rolling(window=10, min_periods=1).mean()
    df["prb_dl_roll_std"]     = dl.rolling(window=10, min_periods=1).std(ddof=0)
    df["prb_ul_roll_std"]     = ul.rolling(window=10, min_periods=1).std(ddof=0)
    df["prb_ul_roll_max"]     = ul.rolling(window=10, min_periods=1).max()
    df["prb_ul_roll_max_100"] = ul.rolling(window=100, min_periods=1).max()
    return df


def check_gaps(df: pd.DataFrame, fname: str) -> int:
    if "timestamp_ms" not in df.columns:
        print(f"  [WARN] kolom timestamp_ms tidak ditemukan di {fname}")
        return 0
    ts = df["timestamp_ms"].astype(float)
    diffs = ts.diff().dropna()
    gaps = (diffs > 1000).sum()
    print(f"  gaps > 1s: {gaps}")
    if gaps > 0:
        print(f"  [WARN] Ada {gaps} gap — rolling stats mungkin tidak sempurna di boundary")
    return gaps


for fpath in FILES:
    print(f"\n=== {fpath} ===")
    if not os.path.exists(fpath):
        print(f"  [SKIP] tidak ditemukan")
        continue

    df = pd.read_csv(fpath)
    print(f"  rows: {len(df)}")

    # Verifikasi sebelum patch
    for col in ROLLING_COLS:
        if col in df.columns:
            nonzero = (df[col] != 0).sum()
            print(f"  {col}: {nonzero} non-zero sebelum patch")

    check_gaps(df, fpath)

    df = compute_rolling_stats(df)

    # Verifikasi setelah patch
    print("  After patch:")
    for col in ROLLING_COLS:
        nonzero = (df[col] != 0).sum()
        pct = 100.0 * nonzero / len(df)
        print(f"    {col}: {nonzero} non-zero ({pct:.1f}%)")

    df.to_csv(fpath, index=False)
    print(f"  [OK] Tersimpan: {fpath}")

print("\nDone. Semua CSV sudah dipatch.")
