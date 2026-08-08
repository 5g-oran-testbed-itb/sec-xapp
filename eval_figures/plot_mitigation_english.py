#!/usr/bin/env python3
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import os

# ─── Configuration ────────────────────────────────────────────────────────────
CSV_PATH = "/home/telmat/sec-xapp/csv/per_ue_training_20260716_171404.csv"
CELL_CSV = "/home/telmat/sec-xapp/csv/training_20260716_171404.csv"
ALERTS_CSV = "/home/telmat/sec-xapp/csv/ue_alerts_20260716_171404.csv"

OUTPUT_DIRS = [
    "/home/telmat/sec-xapp/eval_figures/per_ue_v4",
    "/home/telmat/sec-xapp/eval_figures/per_ue_v7"
]

for d in OUTPUT_DIRS:
    os.makedirs(d, exist_ok=True)

# ─── Color Palette ────────────────────────────────────────────────────────────
C_ATK_THP    = '#D32F2F'   # dark red for attacker throughput
C_ATK_PRB    = '#FF8F00'   # orange for attacker PRB
C_VIC_THP    = '#1565C0'   # blue for victim throughput
C_VIC_PRB    = '#00B0FF'   # cyan for victim PRB
C_ATK_LINE   = '#FF6D00'   # orange dashed event line
C_MIT_LINE   = '#2E7D32'   # dark green dash-dot event line
C_ATK_FILL   = '#FFEBEE'   # very light red for attack active shading
C_MIT_FILL   = '#E8F5E9'   # very light green for mitigation active shading
C_RES_LINE   = '#4A148C'   # purple dash-dot for restore line
C_RES_FILL   = '#E8EAF6'   # light indigo for restore phase shading

# ─── Load Data ────────────────────────────────────────────────────────────────
df = pd.read_csv(CSV_PATH)
t0 = df['timestamp_ms'].min()

# Split by RNTI (RNTI 6 = Attacker, RNTI 5 = Normal UE)
attacker = df[df['rnti'] == 6].copy().reset_index(drop=True)
victim   = df[df['rnti'] == 5].copy().reset_index(drop=True)

attacker['t_sec'] = (attacker['timestamp_ms'] - t0) / 1000.0
victim['t_sec']   = (victim['timestamp_ms'] - t0) / 1000.0

cell_df = pd.read_csv(CELL_CSV)
cell_df['t_sec'] = (cell_df['timestamp_ms'] - t0) / 1000.0

# Convert throughput to Mbps
attacker['thp_ul_mbps'] = attacker['thp_ul_kbps'] / 1000.0
victim['thp_dl_mbps']   = victim['thp_dl_kbps'] / 1000.0

# ─── Key event timestamps (relative seconds) ─────────────────────────────────
t_attack   = 66.0
t_throttle = 70.84
t_restore  = 143.93

print(f"Attack onset:     t = {t_attack:.2f}s")
print(f"Throttle trigger: t = {t_throttle:.2f}s")
print(f"Restore trigger:  t = {t_restore:.2f}s")

# ─── Plot Settings ────────────────────────────────────────────────────────────
plt.rcParams.update({
    'font.family': 'DejaVu Sans',
    'font.size': 11,
    'axes.labelsize': 12,
    'axes.titlesize': 13,
    'legend.fontsize': 10,
    'figure.dpi': 150,
    'savefig.dpi': 300,
    'savefig.bbox': 'tight',
    'axes.grid': True,
    'grid.alpha': 0.3,
    'grid.linestyle': '--',
})

# ═══════════════════════════════════════════════════════════════════════════════
# PLOT 1: Attacker Throughput & PRB Allocation during Attack and Throttle (English)
# ═══════════════════════════════════════════════════════════════════════════════
fig1, ax1 = plt.subplots(figsize=(12, 5))
XLIM1 = (55.0, 115.0)

# Left Y-axis: Throughput
line_thp, = ax1.plot(attacker['t_sec'], attacker['thp_ul_mbps'],
                     color=C_ATK_THP, linewidth=2.2, label='Attacker UL Throughput (Mbps)',
                     zorder=3)
ax1.set_xlabel('Experiment Time (seconds)', fontweight='bold')
ax1.set_ylabel('Uplink Throughput (Mbps)', color=C_ATK_THP, fontweight='bold')
ax1.tick_params(axis='y', labelcolor=C_ATK_THP)
ax1.set_ylim(-1, 30)

# Right Y-axis: PRB Usage Ratio
ax1_prb = ax1.twinx()
line_prb, = ax1_prb.plot(attacker['t_sec'], attacker['prb_usage_ul_ratio'] * 100,
                         color=C_ATK_PRB, linewidth=1.6, linestyle='--',
                         label='Attacker Uplink PRB Allocation (%)', zorder=2)
ax1_prb.set_ylabel('Uplink PRB Allocation (%)', color=C_ATK_PRB, fontweight='bold')
ax1_prb.tick_params(axis='y', labelcolor=C_ATK_PRB)
ax1_prb.set_ylim(-3, 105)
ax1_prb.grid(False)

# Phase shading
ax1.axvspan(t_attack, t_throttle, alpha=0.5, color=C_ATK_FILL, zorder=1)
ax1.axvspan(t_throttle, XLIM1[1], alpha=0.3, color=C_MIT_FILL, zorder=1)

# Event markers
ax1.axvline(x=t_attack, color=C_ATK_LINE, linestyle='--', linewidth=1.8, zorder=4)
ax1.axvline(x=t_throttle, color=C_MIT_LINE, linestyle='-.', linewidth=1.8, zorder=4)

# Annotations
ax1.annotate('Attack Onset\n(Uplink Flood)',
             xy=(t_attack, 15), fontsize=10, fontweight='bold',
             color=C_ATK_LINE, xytext=(t_attack - 8, 22),
             arrowprops=dict(arrowstyle='->', color=C_ATK_LINE, lw=1.5))

ax1.annotate('E2SM-RC Mitigation\nPRB Throttle 5%',
             xy=(t_throttle, 10), fontsize=10, fontweight='bold',
             color=C_MIT_LINE, xytext=(t_throttle + 3, 18),
             arrowprops=dict(arrowstyle='->', color=C_MIT_LINE, lw=1.5))

ax1.annotate('Throughput Throttled ~1.1 Mbps\n(PRB Limited to 5%)',
             xy=(85.0, 1.1), fontsize=10, fontweight='bold',
             color='#37474F', xytext=(90.0, 8.0),
             arrowprops=dict(arrowstyle='->', color='#37474F', lw=1.2))

ax1.set_title('Uplink Throughput and PRB Allocation Profile of Attacker UE (RNTI 6)\nBefore, During, and After Active Mitigation',
              fontsize=13, fontweight='bold', pad=12)
ax1.set_xlim(*XLIM1)

lines = [line_thp, line_prb]
labels = [l.get_label() for l in lines]
ax1.legend(lines, labels, loc='upper right', framealpha=0.95, edgecolor='gray')

fig1.tight_layout()
for d in OUTPUT_DIRS:
    fig1.savefig(os.path.join(d, 'eval_attacker_throughput_en.png'))
print(f"Saved eval_attacker_throughput_en.png to all destinations")
plt.close(fig1)


# ═══════════════════════════════════════════════════════════════════════════════
# PLOT 2: Victim/Normal UE Throughput & PRB Allocation (English)
# ═══════════════════════════════════════════════════════════════════════════════
fig2, ax2 = plt.subplots(figsize=(12, 5))
XLIM2 = (55.0, 115.0)

# Left Y-axis: Throughput
line_vic_thp, = ax2.plot(victim['t_sec'], victim['thp_dl_mbps'],
                         color=C_VIC_THP, linewidth=2.2, label='Co-existing UE DL Throughput (Mbps)',
                         zorder=3)
ax2.set_xlabel('Experiment Time (seconds)', fontweight='bold')
ax2.set_ylabel('Downlink Throughput (Mbps)', color=C_VIC_THP, fontweight='bold')
ax2.tick_params(axis='y', labelcolor=C_VIC_THP)
ax2.set_ylim(-5, 130)

# Right Y-axis: PRB Usage Ratio
ax2_prb = ax2.twinx()
line_vic_prb, = ax2_prb.plot(victim['t_sec'], victim['prb_usage_dl_ratio'] * 100,
                             color=C_VIC_PRB, linewidth=1.6, linestyle='--',
                             label='Co-existing UE Downlink PRB Allocation (%)', zorder=2)
ax2_prb.set_ylabel('Downlink PRB Allocation (%)', color=C_VIC_PRB, fontweight='bold')
ax2_prb.tick_params(axis='y', labelcolor=C_VIC_PRB)
ax2_prb.set_ylim(-3, 110)
ax2_prb.grid(False)

# Reference Attacker Throughput
line_atk_ref, = ax2.plot(attacker['t_sec'], attacker['thp_ul_mbps'],
                         color=C_ATK_THP, linewidth=1.0, alpha=0.25, linestyle=':',
                         label='Ref: Attacker Throughput (Mbps)', zorder=1)

# Phase shading
ax2.axvspan(t_attack, t_throttle, alpha=0.5, color=C_ATK_FILL, zorder=0)
ax2.axvspan(t_throttle, XLIM2[1], alpha=0.3, color=C_MIT_FILL, zorder=0)

# Event markers
ax2.axvline(x=t_attack, color=C_ATK_LINE, linestyle='--', linewidth=1.8, zorder=4)
ax2.axvline(x=t_throttle, color=C_MIT_LINE, linestyle='-.', linewidth=1.8, zorder=4)

# Annotations
ax2.annotate('Data Flooding Attack\n(Attacker Flood Active)',
             xy=(t_attack + 1, 20), fontsize=9, fontweight='bold',
             color=C_ATK_LINE, xytext=(t_attack - 10, 40),
             arrowprops=dict(arrowstyle='->', color=C_ATK_LINE, lw=1.2))

ax2.annotate('Active Co-existing UE Downlink\nPeak Throughput ~114.87 Mbps\n(QoS Preserved During Mitigation)',
             xy=(68.0, 114.87), fontsize=10, fontweight='bold',
             color=C_MIT_LINE, xytext=(72.0, 80.0),
             arrowprops=dict(arrowstyle='->', color=C_MIT_LINE, lw=1.5))

ax2.set_title('Downlink Throughput and PRB Allocation Profile of Normal UE (RNTI 5)\nDuring Attack and Active Mitigation Phases',
              fontsize=13, fontweight='bold', pad=12)
ax2.set_xlim(*XLIM2)

lines = [line_vic_thp, line_vic_prb, line_atk_ref]
labels = [l.get_label() for l in lines]
ax2.legend(lines, labels, loc='upper right', framealpha=0.95, edgecolor='gray')

fig2.tight_layout()
for d in OUTPUT_DIRS:
    fig2.savefig(os.path.join(d, 'eval_victim_throughput_en.png'))
print(f"Saved eval_victim_throughput_en.png to all destinations")
plt.close(fig2)


# ═══════════════════════════════════════════════════════════════════════════════
# PLOT 3: Attacker Restore Phase Throughput & PRB Allocation (English) [NEW]
# ═══════════════════════════════════════════════════════════════════════════════
fig3, ax3 = plt.subplots(figsize=(12, 5))
XLIM3 = (125.0, 175.0)

# Left Y-axis: Throughput
line_res_thp, = ax3.plot(attacker['t_sec'], attacker['thp_ul_mbps'],
                         color=C_ATK_THP, linewidth=2.2, label='Attacker UL Throughput (Mbps)',
                         zorder=3)
ax3.set_xlabel('Experiment Time (seconds)', fontweight='bold')
ax3.set_ylabel('Uplink Throughput (Mbps)', color=C_ATK_THP, fontweight='bold')
ax3.tick_params(axis='y', labelcolor=C_ATK_THP)
ax3.set_ylim(-1, 15)

# Right Y-axis: PRB Usage Ratio
ax3_prb = ax3.twinx()
line_res_prb, = ax3_prb.plot(attacker['t_sec'], attacker['prb_usage_ul_ratio'] * 100,
                         color=C_ATK_PRB, linewidth=1.6, linestyle='--',
                         label='Attacker Uplink PRB Allocation (%)', zorder=2)
ax3_prb.set_ylabel('Uplink PRB Allocation (%)', color=C_ATK_PRB, fontweight='bold')
ax3_prb.tick_params(axis='y', labelcolor=C_ATK_PRB)
ax3_prb.set_ylim(-3, 105)
ax3_prb.grid(False)

# Phase shading
ax3.axvspan(XLIM3[0], t_restore, alpha=0.3, color=C_MIT_FILL, zorder=1)
ax3.axvspan(t_restore, XLIM3[1], alpha=0.2, color=C_RES_FILL, zorder=1)

# Event markers
ax3.axvline(x=t_restore, color=C_RES_LINE, linestyle='-.', linewidth=1.8, zorder=4)

# Annotations
ax3.annotate('RESTORE Command Received\n(Network Recovery)',
             xy=(t_restore, 5.0), fontsize=10, fontweight='bold',
             color=C_RES_LINE, xytext=(t_restore - 10, 8.0),
             arrowprops=dict(arrowstyle='->', color=C_RES_LINE, lw=1.5))

ax3.annotate('Throughput Restored ~10.1 Mbps\n(Unrestricted PRB)',
             xy=(145.0, 10.11), fontsize=10, fontweight='bold',
             color='#37474F', xytext=(148.0, 5.0),
             arrowprops=dict(arrowstyle='->', color='#37474F', lw=1.2))

ax3.set_title('Uplink Throughput Recovery Profile of Attacker UE (RNTI 6)\nFollowing Dispatch of RESTORE Command (Quota Lifted)',
              fontsize=13, fontweight='bold', pad=12)
ax3.set_xlim(*XLIM3)

lines = [line_res_thp, line_res_prb]
labels = [l.get_label() for l in lines]
ax3.legend(lines, labels, loc='upper left', framealpha=0.95, edgecolor='gray')

fig3.tight_layout()
for d in OUTPUT_DIRS:
    fig3.savefig(os.path.join(d, 'eval_attacker_restore_en.png'))
print(f"Saved eval_attacker_restore_en.png to all destinations")
plt.close(fig3)

print("\nAll English plots generated successfully!")
