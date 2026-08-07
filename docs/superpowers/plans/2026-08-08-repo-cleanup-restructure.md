# Repo Cleanup & 3-Node Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `sec-xapp` into a reproducible TA attachment repo: safe local backup first, strip internal thesis-drafting documents and junk from the public tree, split vendor code (FlexRIC/srsRAN) into pinned submodules under `5g-oran-testbed-itb`, reorganize scripts/deploy/observability, and write README documentation covering code, reproduction steps, and 3-node deployment.

**Architecture:** Work happens on a new branch `repo-cleanup` created from the current branch `benign-calibrated-scoring-eval` (confirmed via `git merge-base` to be a strict superset of `master` — `master`'s tip is an ancestor, so branching from the current branch loses nothing relative to `master`) WITHOUT committing the 149 pre-existing uncommitted changes (those get a lossless git-object backup instead, per user instruction). Vendor codebases (FlexRIC, srsRAN_Project) get cleaned and pushed as forks under the `5g-oran-testbed-itb` GitHub org, then wired back as pinned git submodules. Thesis-drafting documents are removed from the tracked tree and added to `.gitignore` by specific pattern (not blanket `*.md`).

**Tech Stack:** git (branch, submodule, rm), bash, ssh/sshpass (read-only investigation + config fetch only), Python (existing scripts, untouched).

**Assumptions made explicit (confirm before Task 1 if any are wrong):**
- Main repo will also be pushed under `5g-oran-testbed-itb` (e.g. `5g-oran-testbed-itb/sec-xapp`) — user gave this org name in response to "where will the restructured repo be pushed."
- Fork names: `5g-oran-testbed-itb/flexric-sec-xapp` and `5g-oran-testbed-itb/srsran-sec-xapp`. These repos must be **created empty on GitHub by the user first** (this plan does not have GitHub API access) — Task 11 has an explicit pause for that.
- No git history rewrite anywhere (no `filter-repo`/`filter-branch`). Old blobs stay in `.git` history; only the working tree shrinks. If the user wants history purged later, that's a separate, explicitly-confirmed operation — not in scope here.
- SSH credentials `telmat@10.91.2.1` (RAN) and `telmat@10.91.2.4` (Core), password `123`, are used read-only in this plan (cat/scp -o) to fetch configs and inspect files — never to modify remote state.

---

## Task 1: Local backup of current uncommitted work

Captures the 149 pending changes losslessly, outside the current branch history, before any destructive operation touches the working tree.

**Files:** none created in the repo; backup lives in `~/sec-xapp-backup-2026-08-08/` and a local git ref.

- [ ] **Step 1: Snapshot tracked modifications as a git object (no commit to any branch, no working-tree change)**

```bash
cd /home/telmat/sec-xapp
git stash create "pre-cleanup snapshot 2026-08-08" > /tmp/stash_hash.txt
cat /tmp/stash_hash.txt
```

Expected: a 40-char commit hash printed and written to `/tmp/stash_hash.txt`. `git stash create` does NOT touch the working tree or the stash list — it only creates a dangling commit object.

- [ ] **Step 2: Pin that object with a ref so git gc can't collect it**

```bash
git update-ref refs/heads/backup/pre-cleanup-2026-08-08 "$(cat /tmp/stash_hash.txt)"
git log -1 --stat backup/pre-cleanup-2026-08-08 | head -20
```

Expected: `git log` shows the snapshot commit with all 30 modified tracked files listed in the diffstat.

- [ ] **Step 3: Copy untracked files to an out-of-repo backup directory, preserving paths**

```bash
mkdir -p ~/sec-xapp-backup-2026-08-08
cd /home/telmat/sec-xapp
git status --porcelain | awk '/^\?\? / {print substr($0,4)}' | sed 's/^"//;s/"$//' > /tmp/untracked_files.txt
wc -l /tmp/untracked_files.txt
rsync -a --files-from=/tmp/untracked_files.txt . ~/sec-xapp-backup-2026-08-08/
```

Expected: `wc -l` shows ~119 files (149 total minus ~30 tracked-modified from Step 1). `rsync` completes without error.

- [ ] **Step 4: Verify backup completeness**

`git status --porcelain` collapses a wholly-untracked directory into a single line, while `find` on the backup lists individual files — comparing those two directly will show a spurious "diff" even when nothing is missing. Use `--untracked-files=all` so both sides are at file granularity:

```bash
diff <(cd /home/telmat/sec-xapp && git status --porcelain --untracked-files=all | awk '/^\?\? / {print substr($0,4)}' | sed 's/^"//;s/"$//' | sort) \
     <(cd ~/sec-xapp-backup-2026-08-08 && find . -type f -printf '%P\n' | sort)
```

Expected: no output, OR a diff showing only extra files present in the backup (not missing ones) — `rsync -a` copies whole untracked directories without respecting `.gitignore`, so gitignored files inside an untracked directory (e.g. `*.csv` results sitting next to tracked outputs) can legitimately appear in the backup but not in git's untracked list. That's fine — the backup being a superset is safe. If any line in the diff is missing *from the backup side* (i.e. a file git considers untracked isn't present in `~/sec-xapp-backup-2026-08-08/`), that's real data loss risk — stop and investigate before proceeding to Task 2.

---

## Task 2: Create the cleanup branch

**Files:** none.

- [ ] **Step 1: Confirm the current branch, then branch from its tip**

```bash
cd /home/telmat/sec-xapp
git branch --show-current
git merge-base master HEAD
git rev-parse master
```

Expected: current branch is `benign-calibrated-scoring-eval`; the two hashes from `merge-base` and `rev-parse master` match (confirms `master` is a strict ancestor — branching from here loses nothing relative to `master`). If they don't match, STOP and report BLOCKED rather than guessing which branch is the right base.

```bash
git branch repo-cleanup
git checkout repo-cleanup
git branch --show-current
```

Expected: `repo-cleanup`. The 149 uncommitted changes remain in the working tree exactly as before (branch creation doesn't touch them) — they now sit on top of `repo-cleanup` instead of `benign-calibrated-scoring-eval`.

- [ ] **Step 2: Confirm the original branch is untouched**

```bash
git log benign-calibrated-scoring-eval -1 --oneline
git diff benign-calibrated-scoring-eval repo-cleanup --stat
```

Expected: `git diff` shows no output (same commit, only working-tree state differs, which git diff between branches doesn't show since working tree isn't part of either branch's history).

---

## Task 3: Curate thesis-drafting documents out of `docs/`

Per user instruction: remove BAB/Buku/T10-T50/prompt/revision/handover/presentation documents from the tracked, public tree. Keep methodology, reproducibility, results, and README-class documents. Two files need content extracted before deletion because they contain reproducibility-relevant facts not duplicated elsewhere.

**Files:**
- Modify: `.gitignore` (add patterns)
- Modify: `docs/MODEL_EVALUATION.md` (receives extracted hyperparameter table)
- Create (temporary, this task only): `deploy/ran/README.md`, `deploy/core/README.md` stub headers (fleshed out fully in Task 16; here they just receive the extracted config/port tables so the source material isn't lost)
- Remove (tracked, via `git rm`): `docs/BAB3.md`, `docs/BAB3_CODE_SNIPPETS.md`, `docs/BAB3_LSTM.md`, `docs/BAB4.md`, `docs/Dokumen T10.docx.md`, `docs/Dokumen-T30-v1.docx.md`, `docs/Draf Dokumen T40.md`, `docs/Referensi Dokumen T40.md`, `docs/Revisi Dokumen T20.docx.md`, `docs/STATUS_DAN_RENCANA_EVALUASI.md`, `docs/STATUS_DAN_RENCANA_EVALUASI_old.md`, `docs/C_XAPP_PROGRESS.md`, `docs/18122041_18122046_PPT Seminar Proposal (1).pdf`, `docs/per_ue_results_slide.html`, `docs/HANDOVER_KPM_PER_UE.md`, `docs/eval_dual_v16_v22.log`
- Remove (untracked, via `rm`): `docs/BAB1.md`, `docs/BAB2.md`, `docs/BAB3_GRU.md`, `docs/BAB4_GRU.md`, `docs/BAB4_LSTM.md`, `docs/BAB5.md`, `docs/PROMPT-RIC-PERBAIKAN-OPSI-B.md`, `docs/PROMPT-RIC-REKALIBRASI.md`, `docs/opsi_b_perbaikan_audit.md`, `docs/perubahan_naskah_opsi_b.md`, `docs/lampiran_hyperparameter_ppt.md` (after extraction)

- [ ] **Step 1: Extract the hyperparameter table from `lampiran_hyperparameter_ppt.md` into `MODEL_EVALUATION.md` before deleting it**

```bash
cd /home/telmat/sec-xapp
grep -n "^#\|^|" docs/lampiran_hyperparameter_ppt.md | head -40
```

Read the output. Append the hyperparameter table(s) found (GRU/LSTM training config: seq_len, hidden dims, dropout, lr, batch size, epochs, patience — same parameters already named in `docs/superpowers/specs/2026-07-29-loss-ablation-design.md:40`) to the end of `docs/MODEL_EVALUATION.md` under a new `## Hyperparameter Final` heading, using `Edit` with the exact table content read above (not paraphrased).

- [ ] **Step 2: Extract config/port tables from `BAB3_CODE_SNIPPETS.md` into deploy READMEs**

```bash
mkdir -p deploy/ran deploy/core
grep -n "^#\|^|" docs/BAB3_CODE_SNIPPETS.md
```

Read the output. Two tables matter for reproducibility and are otherwise lost:
1. The port/interface table (E2AP `:36421`, E42 `:36422`, N3 GTP-U `:2152`) — write it into `deploy/ran/README.md` under `## Interfaces` using `Write`.
2. The `cots_n78_copied.yml` field-mapping table (PLMN, TAC, PCI → config keys) — write it into the same file under `## gNB config fields`.

These two files are stubs for now; Task 16 replaces them with full node READMEs (this step only guarantees the source facts survive the deletion in Step 5).

- [ ] **Step 3: Remove tracked thesis-drafting files**

```bash
cd /home/telmat/sec-xapp
git rm docs/BAB3.md docs/BAB3_CODE_SNIPPETS.md docs/BAB3_LSTM.md docs/BAB4.md \
       "docs/Dokumen T10.docx.md" docs/Dokumen-T30-v1.docx.md \
       "docs/Draf Dokumen T40.md" "docs/Referensi Dokumen T40.md" "docs/Revisi Dokumen T20.docx.md" \
       docs/STATUS_DAN_RENCANA_EVALUASI.md docs/STATUS_DAN_RENCANA_EVALUASI_old.md \
       docs/C_XAPP_PROGRESS.md "docs/18122041_18122046_PPT Seminar Proposal (1).pdf" \
       docs/per_ue_results_slide.html docs/HANDOVER_KPM_PER_UE.md docs/eval_dual_v16_v22.log
```

Expected: 16 files staged for deletion, no errors.

- [ ] **Step 4: Remove untracked thesis-drafting files**

```bash
cd /home/telmat/sec-xapp
rm -f docs/BAB1.md docs/BAB2.md docs/BAB3_GRU.md docs/BAB4_GRU.md docs/BAB4_LSTM.md docs/BAB5.md \
      docs/PROMPT-RIC-PERBAIKAN-OPSI-B.md docs/PROMPT-RIC-REKALIBRASI.md \
      docs/opsi_b_perbaikan_audit.md docs/perubahan_naskah_opsi_b.md docs/lampiran_hyperparameter_ppt.md
```

- [ ] **Step 5: Add gitignore patterns so these categories can't be re-added by accident**

Edit `.gitignore`, append:

```
# Thesis-drafting documents (internal writing process — not part of the
# public reproducibility repo). Technical docs, methodology, and results
# reports are NOT matched by these patterns and stay tracked.
docs/BAB*.md
docs/*Dokumen*.md
docs/PROMPT-*.md
docs/STATUS_DAN_RENCANA*.md
docs/C_XAPP_PROGRESS.md
docs/*Seminar*.pdf
docs/per_ue_results_slide.html
docs/HANDOVER_*.md
docs/opsi_b_perbaikan_audit.md
docs/perubahan_naskah_opsi_b.md
docs/lampiran_hyperparameter_ppt.md
docs/eval_dual_v16_v22.log
```

- [ ] **Step 6: Verify what's left in `docs/` is the intended keep-set**

```bash
cd docs && find . -maxdepth 1 -type f -printf '%f\n' | sort
```

Expected output (21 files): `bobot_benign_calibrated.md`, `CLAUDE.md`, `FEATURE_LIMITATIONS_AND_FUTURE_WORK.md`, `flowchart_1_integrasi.md`, `flowchart_2_deteksi.md`, `flowchart_3_mitigasi.md`, `flowchart_sistem_terbaru.md`, `flowchart_sistem_terbaru.svg`, `Flowchart Subsistem Deteksi Anomali Hibrida.png`, `implementasi-overall.png`, `loss_ablation_results.md`, `mitigasi.md`, `MODEL_EVALUATION.md`, `open5gs_mitigation_walkthrough.md`, `opsi_b_metrics.md`, `opsi_b_recalibration_results.md`, `OPSI-B-REKALIBRASI.md`, `oran_mitigation_flow.md`, `panduan_analisis_mitigasi.md`, `per_ue_v5_results.md`, `per_ue_v6_results.md`, `PRD_Security_xApp.md`, `README.md`, `scoring_comparison_results.md`, `XAPP_C_MIGRATION_DESIGN.md` (plus `superpowers/` subdir, untouched).

> **Confirm-before-continue:** `PRD_Security_xApp.md` is a borderline call — kept here because it reads as a requirements/methodology spec rather than a narrative status update, but flag it to the user for a quick look during Task 16 (root README writing) in case it should also go.

- [ ] **Step 7: Commit**

```bash
cd /home/telmat/sec-xapp
git add .gitignore docs/MODEL_EVALUATION.md deploy/ran/README.md deploy/core/README.md
git status --short docs/ | head -20
git commit -m "$(cat <<'EOF'
chore(docs): remove thesis-drafting documents from public tree

BAB chapters, T10-T50 drafts, revision/prompt notes, and presentation
artifacts are internal writing-process documents, not reproducibility
material. Extracted the two reproducibility-relevant tables they held
(hyperparameters, gNB config field mapping) into MODEL_EVALUATION.md
and deploy/ran|core/README.md stubs before removal. .gitignore now
blocks these categories by specific pattern.
EOF
)"
```

---

## Task 4: Security fix — remove hardcoded SSH passwords

**Files:**
- Modify: `patch_core.sh`
- Modify: `sync_gnb_config.sh`
- Remove: `patch_and_rebuild_gnb.sh` (stale one-off script — see rationale below)

- [ ] **Step 1: Confirm the anti-crash patch this script applied is already permanent in the srsRAN fork**

```bash
ssh -o StrictHostKeyChecking=no telmat@10.91.2.1 \
  "cd /home/telmat/TA-Rizqi-Nabiel/O-RAN-Testbed-Automation/Next_Generation_Node_B/srsRAN_Project && \
   git diff --stat -- lib/e2/e2sm/e2sm_kpm/e2sm_kpm_report_service_impl.cpp"
```

Expected: non-empty diffstat (the bounds-checking patch is present as a local modification, matching what was found during brainstorming). Since it's already baked into the working tree that Task 12 will fork and pin, `patch_and_rebuild_gnb.sh` (which re-applies a patch from a now-nonexistent scratch path `/home/telmat/.gemini/antigravity/brain/.../patch_gnb_kpm.py`, using a hardcoded password) is dead and unsafe. Delete it rather than fix it.

```bash
cd /home/telmat/sec-xapp
rm -f patch_and_rebuild_gnb.sh
```

- [ ] **Step 2: Replace hardcoded password in `patch_core.sh` with an env-var + SSH-key precondition**

Read current top of file first:

```bash
sed -n '1,10p' patch_core.sh
```

Edit `patch_core.sh`, change:

```bash
CORE_IP="10.91.2.4"
CORE_USER="telmat"
CORE_PASS="123"
```

to:

```bash
CORE_IP="${CORE_IP:-10.91.2.4}"
CORE_USER="${CORE_USER:-telmat}"
# Requires an SSH key already authorized on $CORE_USER@$CORE_IP (ssh-copy-id).
# No password is read from this script or the environment.
```

Then find every `sshpass -p "$CORE_PASS" ssh` / `sshpass -p "$CORE_PASS" scp` call in the file:

```bash
grep -n "sshpass" patch_core.sh
```

Replace each occurrence of `sshpass -p "$CORE_PASS" ssh -o StrictHostKeyChecking=no` with `ssh -o StrictHostKeyChecking=no`, and each `sshpass -p "$CORE_PASS" scp -o StrictHostKeyChecking=no` with `scp -o StrictHostKeyChecking=no` (i.e., drop the `sshpass -p "$CORE_PASS"` prefix entirely — key-based auth needs no password wrapper).

- [ ] **Step 3: Same fix in `sync_gnb_config.sh`**

```bash
cat sync_gnb_config.sh
```

Edit to remove the `sshpass -p "123"` prefix from the `scp` call, and change the hardcoded `telmat@10.91.2.1` destination to use a `GNB_USER`/`GNB_IP` variable pair the same way as Step 2, for consistency.

- [ ] **Step 4: Verify no literal passwords remain**

```bash
cd /home/telmat/sec-xapp
grep -rn "sshpass\|PASS=\"123\"\|-p \"123\"" --include="*.sh" .
```

Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add patch_core.sh sync_gnb_config.sh
git rm patch_and_rebuild_gnb.sh
git commit -m "$(cat <<'EOF'
security(deploy): remove hardcoded SSH passwords

patch_core.sh and sync_gnb_config.sh used sshpass with a literal
password that was verified to still work against the live Core node.
Switched to key-based auth via env-var-overridable host/user.

patch_and_rebuild_gnb.sh deleted: it re-applied a one-off KPM
bounds-check patch from a scratch path that no longer exists: the
patch is already permanent in the srsRAN fork (Task 12), and the
script also embedded the same hardcoded password.
EOF
)"
```

---

## Task 5: Remove miscellaneous junk

**Files:** removed only, no modifications.

- [ ] **Step 1: Remove personal screenshots, stray artifacts, and an unrelated personal script**

```bash
cd /home/telmat/sec-xapp
rm -f Critical.png Warning.png "Screenshot 2026-07-02 at 18.08.27.png" \
      "Screenshot 2026-07-02 at 18.08.31.png" "Screenshot 2026-07-02 at 18.08.41.png" \
      "Screenshot 2026-07-02 at 20.04.44.png" "Screenshot 2026-07-02 at 20.05.04.png" \
      "Screenshot 2026-07-02 at 20.05.06.png" "Screenshot 2026-07-02 at 20.05.12.png" \
      "Screenshot 2026-07-02 at 20.05.18.png" "Screenshot 2026-07-04 at 18.16.33.png" \
      "Screenshot 2026-08-07 at 23.43.46.png" \
      grafana_session= pidstat_overhead.log~ test.csv restore_history.py
```

`restore_history.py` restores a VS Code / Antigravity IDE state database at `/home/telmat/.config/...` — unrelated to this project, a personal tool that leaked into the repo.

- [ ] **Step 2: Remove build/cache artifacts**

```bash
cd /home/telmat/sec-xapp
rm -rf __pycache__ .pytest_cache tests/__pycache__ exporter/__pycache__ testing_app/__pycache__
```

- [ ] **Step 3: Remove the broken FlexRIC gitlink**

```bash
cd /home/telmat/sec-xapp
git rm --cached flexric
rm -rf flexric
```

This is the 901 MB checkout that has no `xapp_sec_moni.c` and no `.gitmodules` entry — the real, patched FlexRIC checkout lives at `~/flexric` and gets forked properly in Task 12.

- [ ] **Step 4: Remove the duplicate xApp rebuild script**

```bash
cd /home/telmat/sec-xapp
diff <(grep -v '^#' rebuild_xapp.sh) <(grep -v '^#' rebuild_xapp_user.sh)
```

`rebuild_xapp_user.sh` builds both `xapp_sec_mitigate` and `xapp_sec_moni`; `rebuild_xapp.sh` builds only the former — a strict subset. Remove the subset:

```bash
rm -f rebuild_xapp.sh
```

- [ ] **Step 5: Verify the PDF reference (open question §8.2 from the design spec)**

```bash
grep -rln "Anomaly_Detection_for_Mitigating" --include="*.md" docs/ 2>/dev/null
```

Expected: no output (nothing in the kept doc set cites it — it was only ever referenced by the removed status/handover documents). Remove it:

```bash
rm -f "Anomaly_Detection_for_Mitigating_xApp_and_E2_Interface_Threats_in_O-RAN_Near-RT_RIC.pdf"
```

If the grep above *does* find a reference, stop and ask the user whether the PDF should move to `docs/` as a citation instead of being deleted.

- [ ] **Step 6: Update `.gitignore` for the junk categories that could recur**

Append to `.gitignore`:

```
*~
grafana_session=
```

(`__pycache__/`, `.pytest_cache/`, `*.log` are already covered by existing rules.)

- [ ] **Step 7: Commit**

```bash
cd /home/telmat/sec-xapp
git add -u
git add .gitignore
git status --short
git commit -m "$(cat <<'EOF'
chore: remove screenshots, caches, broken flexric gitlink, dead scripts

flexric/ gitlink pointed at a checkout with no xapp_sec_moni.c and no
.gitmodules entry — dead weight, superseded by the proper submodule
added in a later task. restore_history.py is an unrelated personal
IDE-state tool. rebuild_xapp.sh is a strict subset of
rebuild_xapp_user.sh.
EOF
)"
```

---

## Task 6: Investigate RAN `gnb.yaml` vs `cots_n78_copied.yml`

Resolves open question §8.1 from the design spec: does `configs/gnb.yaml` (what `run.sh` actually launches with) include `cots_n78_copied.yml`, or are they independent?

**Files:** none (read-only investigation); result feeds Task 15's `deploy/ran/README.md`.

- [ ] **Step 1: Read both files on the RAN node**

```bash
ssh -o StrictHostKeyChecking=no telmat@10.91.2.1 \
  "cat /home/telmat/TA-Rizqi-Nabiel/O-RAN-Testbed-Automation/Next_Generation_Node_B/configs/gnb.yaml" \
  > /tmp/gnb_yaml_content.txt
ssh -o StrictHostKeyChecking=no telmat@10.91.2.1 \
  "cat /home/telmat/TA-Rizqi-Nabiel/O-RAN-Testbed-Automation/Next_Generation_Node_B/configs/cots_n78_copied.yml" \
  > /tmp/cots_n78_copied_content.txt
grep -n "include\|cots_n78\|import\|!include" /tmp/gnb_yaml_content.txt
```

- [ ] **Step 2: Diff the two to establish the actual relationship**

```bash
diff /tmp/gnb_yaml_content.txt /tmp/cots_n78_copied_content.txt | head -60
wc -l /tmp/gnb_yaml_content.txt /tmp/cots_n78_copied_content.txt
```

Three possible outcomes, each with a concrete next action:
- **`gnb.yaml` textually includes/references `cots_n78_copied.yml`** (grep in Step 1 found a hit) → note the include mechanism in `deploy/ran/README.md` (Task 15).
- **Files are near-identical / `gnb.yaml` is a generated copy of `cots_n78_copied.yml`** (diff is empty or trivial) → note in `deploy/ran/README.md` that `run.sh` was pointed at a renamed copy of the same config; only `cots_n78_copied.yml` needs to be tracked as the source of truth.
- **Files are substantively different** (diff shows many differing keys) → both configs must be captured in `deploy/ran/configs/` (Task 13), and `deploy/ran/README.md` must explain which one is actually live (`gnb.yaml`, since that's what `run.sh` launches) versus which one BAB3 documented (`cots_n78_copied.yml`) — this is a documentation-accuracy issue worth a explicit callout, not silently picking one.

Record the finding as a one-paragraph note; it's consumed directly by Task 15, Step 2.

---

## Task 7: Audit and clean up model checkpoint versions

**Files:**
- Remove (tracked): superseded `security_model_v*.onnx(.data)` in root, superseded `lstm_autoencoder_v*.pt/.json` in `models/`, superseded `gru_ue_v1-v4` / `lstm_ue_v1-v4` in `models/`
- Keep: everything still referenced by active code (verified below), plus the final per-UE v5/v6 models

- [ ] **Step 1: Build a reference map — for every `security_model*.onnx`, `lstm_autoencoder_v*.pt`, `gru_ue_v*`, `lstm_ue_v*` file, find what (if anything) references it outside `docs/` and `.claude/worktrees/`**

```bash
cd /home/telmat/sec-xapp
for f in security_model*.onnx security_model*.onnx.data models/lstm_autoencoder_v*.pt models/gru_ue_v*.pt models/lstm_ue_v*.pt models/gru_autoencoder_*_v1.pt; do
  [ -e "$f" ] || continue
  base=$(basename "$f")
  refs=$(grep -rl -- "$base" --include="*.py" --include="*.sh" --include="*.c" --include="*.conf" --include="*.yml" . 2>/dev/null | grep -v "^./docs/\|\.claude/worktrees\|^\./$base$")
  echo "$base: ${refs:-<no references found>}"
done
```

- [ ] **Step 2: Cross-check against what's already known to be live (from brainstorming investigation)**

Known-live from prior investigation (must NOT be deleted regardless of Step 1 output):
- `security_model.onnx` / `.onnx.data` — unversioned, `docker-compose.yml` `ONNX_MODEL` default, `evaluate_detection.py:21`
- `security_model_v16.onnx` / `.onnx.data` — hardcoded in `copy-xapp/xapp_sec_moni.c:265` (**production xApp path**)
- `security_model_v16_raw.onnx` — default `--lstm-a` in `evaluate_gru.py:224`
- `security_model_v22.onnx` / `.onnx.data` — default `--lstm-b` in `evaluate_gru.py:226`, `evaluate_detection.py:737`
- `models/gru_autoencoder_A_v1.pt`, `models/gru_autoencoder_B_v1.pt` — `docker-compose.yml` `GRU_MODEL_A`/`GRU_MODEL_B`, `exporter/csv_exporter.py:243-244`
- `models/gru_ue_v4.pt`/`.onnx`, `models/lstm_ue_v4.onnx` — `docker-compose.yml` testing-app volumes
- `models/gru_ue_v5*`, `models/gru_ue_v6*`, `models/lstm_ue_v5*`, `models/lstm_ue_v6*` — current per-UE deploy models, `copy-xapp/xapp_sec_moni.c:165,171`

- [ ] **Step 3: Present the candidate-delete list for a quick confirmation before removing anything**

Candidate deletions (present this list, get explicit "go ahead" before Step 4 — checkpoint files are effectively gone once removed from the working tree even though history keeps old blobs):
```
security_model_v13.onnx(.data), security_model_v15.onnx(.data),
security_model_v17.onnx(.data), security_model_v17_raw.onnx(.data),
security_model_v18.onnx(.data), security_model_v18_raw.onnx(.data),
security_model_v19.onnx(.data), security_model_v19_raw.onnx(.data),
security_model_v20.onnx(.data), security_model_v20_raw.onnx(.data),
security_model_v21.onnx(.data), security_model_v23.onnx(.data), security_model_v24.onnx(.data),
security_model_gru_A.onnx(.data), security_model_gru_B.onnx(.data)  [unreferenced per Step 1],
models/lstm_autoencoder_v3.pt through v24.pt (+ _losses.json, _threshold.json, _threshold_recal.json)
  except v16 (kept, no .pt exists at root for it — the ONNX export is what's referenced, not a .pt),
models/gru_ue_v1.pt/.onnx/.onnx.data/_scaler.pkl/_threshold.json,
models/gru_ue_v2_scaler.pkl, models/gru_ue_v3*, models/lstm_ue_v1*, models/lstm_ue_v2_scaler.pkl, models/lstm_ue_v3*
```

- [ ] **Step 4: Execute deletion (only the entries confirmed in Step 3)**

```bash
cd /home/telmat/sec-xapp
git rm -f security_model_v13.onnx security_model_v13.onnx.data \
          security_model_v15.onnx security_model_v15.onnx.data \
          security_model_v17.onnx security_model_v17.onnx.data security_model_v17_raw.onnx security_model_v17_raw.onnx.data \
          security_model_v18.onnx security_model_v18.onnx.data security_model_v18_raw.onnx security_model_v18_raw.onnx.data \
          security_model_v19.onnx security_model_v19.onnx.data security_model_v19_raw.onnx security_model_v19_raw.onnx.data \
          security_model_v20.onnx security_model_v20.onnx.data security_model_v20_raw.onnx security_model_v20_raw.onnx.data \
          security_model_v21.onnx security_model_v21.onnx.data \
          security_model_v23.onnx security_model_v23.onnx.data \
          security_model_v24.onnx security_model_v24.onnx.data \
          security_model_gru_A.onnx security_model_gru_A.onnx.data \
          security_model_gru_B.onnx security_model_gru_B.onnx.data
git rm -f models/lstm_autoencoder_v{3,4,5,6,7,8,9,10,11,12,13,14,15,17,18,19,20,21,22,23,24}.pt
git rm -f models/lstm_autoencoder_v{3,4,5,6,7,8,9,10,11,12,13,14,15,17,18,19,20,21,22,23,24}_losses.json
git rm -f models/lstm_autoencoder_v{14,16,17,18,19,20,21,22}_threshold_recal.json
git rm -f models/gru_ue_v1.pt models/gru_ue_v1.onnx models/gru_ue_v1.onnx.data models/gru_ue_v1_scaler.pkl models/gru_ue_v1_threshold.json models/gru_ue_v1_losses.json models/gru_ue_v1_train.log
git rm -f models/gru_ue_v2_scaler.pkl
git rm -f models/gru_ue_v3.pt models/gru_ue_v3_scaler.pkl models/gru_ue_v3_threshold.json models/gru_ue_v3_losses.json models/gru_ue_v3_weights.json
git rm -f models/lstm_ue_v1.pt models/lstm_ue_v1.onnx models/lstm_ue_v1.onnx.data models/lstm_ue_v1_scaler.pkl models/lstm_ue_v1_threshold.json models/lstm_ue_v1_losses.json models/lstm_ue_v1_train.log
git rm -f models/lstm_ue_v2_scaler.pkl
git rm -f models/lstm_ue_v3.pt models/lstm_ue_v3_scaler.pkl models/lstm_ue_v3_threshold.json models/lstm_ue_v3_losses.json models/lstm_ue_v3_weights.json
```

Note: `models/lstm_autoencoder_v16.pt` is kept even though only the ONNX export (`security_model_v16.onnx`, at root) is referenced by the live C xApp — the `.pt` is the training checkpoint that produced it and is small (~450KB); keeping it preserves the ability to re-export without retraining.

- [ ] **Step 5: Verify size reduction**

```bash
du -sh models/ security_model*.onnx* 2>/dev/null
```

- [ ] **Step 6: Commit**

```bash
git commit -m "$(cat <<'EOF'
chore(models): remove superseded checkpoint versions

Kept only checkpoints referenced by live code: security_model.onnx
(docker-compose/eval default), security_model_v16(_raw).onnx
(hardcoded in xapp_sec_moni.c and evaluate_gru.py), security_model_v22.onnx
(evaluate_gru.py/evaluate_detection.py default), gru_autoencoder_A/B_v1
(docker-compose GRU ensemble), and gru_ue/lstm_ue v4-v6 (deploy + testing-app
volumes). Removed 20+ intermediate LSTM/GRU training iterations.
EOF
)"
```

---

## Task 8: Restructure `scripts/`

**Files:**
- Create: `scripts/train/`, `scripts/eval/`, `scripts/plot/`, `scripts/export/`, `scripts/data/`
- Move (via `git mv`): all root-level `.py` files except `src/` internals

- [ ] **Step 1: Create the directories and move training scripts**

```bash
cd /home/telmat/sec-xapp
mkdir -p scripts/train scripts/eval scripts/plot scripts/export scripts/data
git mv train_gru.py train_gru_ue.py train_lstm.py train_lstm_ue.py scripts/train/
```

- [ ] **Step 2: Move evaluation scripts**

```bash
git mv calibrate_threshold_gru.py calibrate_threshold_remote.py derive_loss_weights.py \
       eval_loss_ablation.py eval_opsi_b.py evaluate_detection.py evaluate_gru.py \
       evaluate_per_ue_v2.py evaluate_scoring_comparison.py evaluate_ue_models.py \
       sweep_gru_threshold.py verify_scoring_math.py aggregate_grouped_ablation.py \
       run_grouped_ablation.py \
       scripts/eval/
```

- [ ] **Step 3: Move plotting scripts**

```bash
git mv plot_benign_calibrated.py plot_grouped_ablation.py plot_learning_curves_uniform.py \
       plot_learning_curves_v5.py plot_learning_curves_v6.py plot_loss_ablation.py \
       plot_opsi_b.py plot_training_evaluation.py \
       scripts/plot/
```

- [ ] **Step 4: Move export scripts**

```bash
git mv export_onnx.py export_onnx_gru.py export_onnx_ue.py run_export_gru.sh scripts/export/
```

- [ ] **Step 5: Move the one remaining data-utility script**

```bash
git mv patch_rolling_stats.py scripts/data/
```

- [ ] **Step 6: Fix internal script references broken by the move**

```bash
grep -rn "^\./venv/bin/python3 export_onnx" scripts/export/run_export_gru.sh
```

Edit `scripts/export/run_export_gru.sh` — it references `models/gru_ue_v5.pt` etc. with paths relative to repo root, but the script itself moved two directories deeper. Change:
```bash
./venv/bin/python3 export_onnx_ue.py --arch gru --model models/gru_ue_v5.pt --scaler models/gru_ue_v5_scaler.pkl --out models/gru_ue_v5.onnx
```
to:
```bash
cd "$(dirname "$0")/../.." || exit 1
./venv/bin/python3 scripts/export/export_onnx_ue.py --arch gru --model models/gru_ue_v5.pt --scaler models/gru_ue_v5_scaler.pkl --out models/gru_ue_v5.onnx
```

- [ ] **Step 7: Check whether any moved `.py` files import each other by relative path (would break)**

```bash
grep -rln "^from \(train_\|export_onnx\|evaluate_\|plot_\)\|^import \(train_\|export_onnx\|evaluate_\|plot_\)" scripts/
```

If this finds hits, note them — Python same-directory imports won't resolve across the new `train/`/`eval/`/`plot/` split. Expected: no output, since these scripts were designed as standalone CLI entry points against `src/detection/` (verified during Task-0 exploration — none of them import each other).

- [ ] **Step 8: Run one moved script to confirm it still executes from repo root**

```bash
cd /home/telmat/sec-xapp
./venv/bin/python3 scripts/eval/verify_scoring_math.py --help 2>&1 | head -5
```

Expected: argparse help output, not an import error.

- [ ] **Step 9: Commit**

```bash
git add scripts/
git commit -m "refactor: group root-level scripts into scripts/{train,eval,plot,export,data}/"
```

---

## Task 9: Restructure `deploy/{ric,ran,core}/`

**Files:**
- Create: `deploy/ric/`, `deploy/ran/` (configs stub from Task 3 already exists), `deploy/core/` (stub from Task 3 already exists)
- Move: RIC/RAN/Core-specific scripts and configs

- [ ] **Step 1: Move RIC-node scripts and configs**

```bash
cd /home/telmat/sec-xapp
mkdir -p deploy/ric
git mv start_xapp_c.sh start_xapp_c_mitigate.sh start_xapp_c_mitigate_bg.sh \
       start_xapp_automated.sh start_bg.sh stop_xapp.sh \
       rebuild_xapp_user.sh record_dataset.sh run_automation.sh \
       my_xapp_kpm.conf my_xapp_mitigate.conf \
       deploy/ric/
```

- [ ] **Step 2: Move RAN-node scripts and configs**

```bash
git mv sync_gnb_config.sh cots_n78_copied.yml gnb_usrp.yaml deploy/ran/
mkdir -p deploy/ran/configs
git mv deploy/ran/cots_n78_copied.yml deploy/ran/gnb_usrp.yaml deploy/ran/configs/
```

- [ ] **Step 3: Move Core-node scripts**

```bash
git mv patch_core.sh change_subscriber_slice.sh deploy/core/
```

- [ ] **Step 4: Fix path references inside moved RIC scripts**

```bash
grep -n "my_xapp_kpm.conf\|my_xapp_mitigate.conf\|/home/telmat/sec-xapp" deploy/ric/*.sh
```

For each hit referencing a bare `my_xapp_kpm.conf` (relative to old root), update to `deploy/ric/my_xapp_kpm.conf` if the script is meant to be run from repo root, or leave as-is with a comment if the script is meant to be run from inside `deploy/ric/` — check each script's existing `cd` behavior first:

```bash
grep -n "^cd \|^set -e" deploy/ric/start_xapp_c.sh deploy/ric/record_dataset.sh deploy/ric/run_automation.sh
```

Apply the fix pattern seen in Task 8 Step 6 (`cd "$(dirname "$0")/../.."` at the top, then repo-root-relative paths) to any script found referencing `my_xapp_kpm.conf`, `my_xapp_mitigate.conf`, or `models/` without an absolute path.

- [ ] **Step 5: Verify no script silently broke — dry-run each moved shell script's `--help` or argument-parsing path where available**

```bash
bash -n deploy/ric/*.sh deploy/ran/*.sh deploy/core/*.sh
```

Expected: no syntax errors reported (this catches quoting/path mistakes made during editing, not logic errors — full functional test requires the live testbed, out of scope for this plan).

- [ ] **Step 6: Commit**

```bash
git add deploy/
git commit -m "refactor: group RIC/RAN/Core deployment scripts under deploy/{ric,ran,core}/"
```

---

## Task 10: Restructure `observability/`

**Files:**
- Create: `observability/`
- Move: `docker-compose.yml`, `grafana/`, `prometheus/`, `exporter/`, `testing_app/`

- [ ] **Step 1: Move the directories**

```bash
cd /home/telmat/sec-xapp
mkdir -p observability
git mv docker-compose.yml grafana prometheus exporter testing_app observability/
```

- [ ] **Step 2: Fix `docker-compose.yml` build contexts and volume paths for the new depth**

Read current volumes first:

```bash
grep -n "build:\|volumes:\|- \./" observability/docker-compose.yml
```

Every `./exporter`, `./testing_app`, `./prometheus/prometheus.yml`, `./grafana/provisioning` path is now correct as-is (they moved together with `docker-compose.yml`, staying relative siblings). But `./csv`, `./results`, `./models`, `./security_model.onnx`, `./security_model.onnx.data` referenced volumes that are now **one level up**. Edit `observability/docker-compose.yml`, change each of those five lines from `./X` to `../X`:

```yaml
      - ../csv:/data/csv:ro
      - ../results:/data/results:ro
      - ../models:/data/models:ro
      - ../security_model.onnx:/data/security_model.onnx:ro
      - ../security_model.onnx.data:/data/security_model.onnx.data:ro
```

and similarly for the `testing-app` service's `../models/gru_ue_v4.onnx`, `../models/lstm_ue_v4.onnx` volumes.

- [ ] **Step 3: Verify docker-compose config is still syntactically valid**

```bash
cd /home/telmat/sec-xapp/observability
docker compose config --quiet
echo "exit: $?"
```

Expected: `exit: 0`, no error output. (If `docker compose` isn't installed in this environment, at minimum run `python3 -c "import yaml; yaml.safe_load(open('docker-compose.yml'))"` to confirm valid YAML.)

- [ ] **Step 4: Commit**

```bash
cd /home/telmat/sec-xapp
git add observability/
git commit -m "refactor: move docker-compose stack into observability/, fix relative volume paths"
```

---

## Task 11: Push vendor forks — FlexRIC and srsRAN_Project

**This task requires two manual actions from the user before the commands below will work: create two empty repos on GitHub under the `5g-oran-testbed-itb` org — `flexric-sec-xapp` and `srsran-sec-xapp` — with no README/license/gitignore auto-initialized.** Pause here and confirm both exist before running Step 3 / Step 7.

**Files:** none in `sec-xapp` (this task operates on `~/flexric` and the RAN node's `srsRAN_Project`, outside this repo).

- [ ] **Step 1: Clean the FlexRIC working tree of stray files before committing patches**

```bash
cd /home/telmat/flexric
git status --short | grep '^??'
rm -f "examples/xApp/c/monitor/xapp_sec_moni (Copy 2).c" "examples/xApp/c/monitor/xapp_sec_moni (Copy).c" \
      src/sm/rc_sm/dec/rc_dec_asn.c.backup compile_commands.json
```

- [ ] **Step 2: Commit the 822-line patch with a descriptive message**

```bash
cd /home/telmat/flexric
git add examples/xApp/c/monitor/ src/lib/e2ap/v2_03/enc/e2ap_msg_enc_asn.c \
        src/ric/iApp/e42_iapp.c src/ric/iApp/msg_handler_iapp.c src/ric/iApp/msg_handler_iapp.h \
        src/ric/map_e2_node_sockaddr.c src/ric/msg_handler_ric.c src/ric/near_ric.c \
        src/sm/kpm_sm/kpm_sm_v03.00/dec/kpm_dec_asn.c src/sm/kpm_sm/kpm_sm_v03.00/dec/kpm_dec_asn.h \
        src/util/conf_file.c src/xApp/e42_xapp.c src/xApp/sync_ui.c
git status --short
git commit -m "$(cat <<'EOF'
feat: security xApp (xapp_sec_moni/xapp_sec_mitigate) + per-UE KPM support

Adds the C-native anomaly detection xApp (rule-based + LSTM/GRU-AE
ONNX inference), per-UE tracking (ue_tracker.c), E2SM-RC mitigation
(xapp_sec_mitigate.c), and core FlexRIC changes required to support
per-UE KPM style 4/5 reporting and iApp message routing for the
security xApp's IPC mitigation channel.
EOF
)"
```

- [ ] **Step 3: Add the GitHub remote and push**

```bash
cd /home/telmat/flexric
git remote add security-fork https://github.com/5g-oran-testbed-itb/flexric-sec-xapp.git
git push security-fork HEAD:main
```

Expected: push succeeds, prints the new branch ref on GitHub.

- [ ] **Step 4: Record the exact commit pinned**

```bash
cd /home/telmat/flexric
git rev-parse HEAD > /tmp/flexric_pin_commit.txt
cat /tmp/flexric_pin_commit.txt
```

- [ ] **Step 5: Same cleanup for `srsRAN_Project` on the RAN node — check for stray files first**

```bash
ssh -o StrictHostKeyChecking=no telmat@10.91.2.1 \
  "cd /home/telmat/TA-Rizqi-Nabiel/O-RAN-Testbed-Automation/Next_Generation_Node_B/srsRAN_Project && git status --short"
```

Review output; if there are stray `.backup`/`(Copy)` files analogous to FlexRIC's, remove them the same way via SSH before committing.

- [ ] **Step 6: Commit the 3-file patch on the RAN node**

```bash
ssh -o StrictHostKeyChecking=no telmat@10.91.2.1 "cd /home/telmat/TA-Rizqi-Nabiel/O-RAN-Testbed-Automation/Next_Generation_Node_B/srsRAN_Project && \
  git add lib/du/du_high/du_manager/du_ue/du_ue_manager.cpp \
          lib/e2/e2sm/e2sm_kpm/e2sm_kpm_du_meas_provider_impl.cpp \
          lib/e2/e2sm/e2sm_kpm/e2sm_kpm_report_service_impl.cpp && \
  git commit -m 'feat(e2sm-kpm): full per-UE F1AP iteration + bounds-checked style 4/5 collection

Adds f1ap_ue_id_translator lookup in du_ue_manager so per-UE metrics
resolve to the correct du_ue_index. Changes the KPM measurement
provider to iterate all registered F1AP UEs (0..MAX_NOF_DU_UES)
instead of a partial match, so per-UE KPM is captured completely.
Adds bounds checking to collect_measurements() for report styles
4 and 5 to prevent a segfault when the measurement-record count
does not match the reported UE count.'"
```

- [ ] **Step 7: Push the RAN node's srsRAN fork**

```bash
ssh -o StrictHostKeyChecking=no telmat@10.91.2.1 "cd /home/telmat/TA-Rizqi-Nabiel/O-RAN-Testbed-Automation/Next_Generation_Node_B/srsRAN_Project && \
  git remote add security-fork https://github.com/5g-oran-testbed-itb/srsran-sec-xapp.git && \
  git push security-fork HEAD:main"
```

(This push runs from the RAN node's own SSH session, using whatever git credentials are already configured there — if none are, the user needs to authenticate first; note this to the user rather than embedding a token.)

- [ ] **Step 8: Record the pinned commit**

```bash
ssh -o StrictHostKeyChecking=no telmat@10.91.2.1 "cd /home/telmat/TA-Rizqi-Nabiel/O-RAN-Testbed-Automation/Next_Generation_Node_B/srsRAN_Project && git rev-parse HEAD" > /tmp/srsran_pin_commit.txt
cat /tmp/srsran_pin_commit.txt
```

---

## Task 12: Add FlexRIC and srsRAN as pinned submodules

**Files:**
- Create: `.gitmodules`, `vendor/flexric` (submodule), `vendor/srsran` (submodule)

- [ ] **Step 1: Add both submodules**

```bash
cd /home/telmat/sec-xapp
mkdir -p vendor
git submodule add https://github.com/5g-oran-testbed-itb/flexric-sec-xapp.git vendor/flexric
git submodule add https://github.com/5g-oran-testbed-itb/srsran-sec-xapp.git vendor/srsran
```

- [ ] **Step 2: Pin each to the exact commit recorded in Task 11**

```bash
cd /home/telmat/sec-xapp/vendor/flexric
git checkout "$(cat /tmp/flexric_pin_commit.txt)"
cd /home/telmat/sec-xapp/vendor/srsran
git checkout "$(cat /tmp/srsran_pin_commit.txt)"
cd /home/telmat/sec-xapp
git add vendor/flexric vendor/srsran
```

- [ ] **Step 3: Verify `.gitmodules` is well-formed**

```bash
cat .gitmodules
git submodule status
```

Expected: two entries, both showing the pinned commit hash with no leading `-` (which would mean "not initialized") or `+` (which would mean "checked out commit differs from pinned").

- [ ] **Step 4: Commit**

```bash
git add .gitmodules vendor/
git commit -m "$(cat <<'EOF'
feat: add FlexRIC and srsRAN as pinned submodules

vendor/flexric -> 5g-oran-testbed-itb/flexric-sec-xapp @ pinned commit
vendor/srsran  -> 5g-oran-testbed-itb/srsran-sec-xapp @ pinned commit

Replaces the broken flexric/ gitlink removed in an earlier commit.
Both forks carry the local patches on top of their respective
upstreams (EURECOM FlexRIC, srsRAN_Project) with full commit history.
EOF
)"
```

---

## Task 13: Pull live configs from RAN and Core nodes

**Files:**
- Create/populate: `deploy/ran/configs/` (already has `cots_n78_copied.yml`, `gnb_usrp.yaml` from Task 9 — add `gnb.yaml` per Task 6's finding)
- Create/populate: `deploy/core/config/`
- Create: `deploy/core/UPSTREAM_COMMIT.txt`

- [ ] **Step 1: Fetch `gnb.yaml` from the RAN node if Task 6 determined it's independent (not a renamed copy)**

```bash
scp -o StrictHostKeyChecking=no \
  telmat@10.91.2.1:/home/telmat/TA-Rizqi-Nabiel/O-RAN-Testbed-Automation/Next_Generation_Node_B/configs/gnb.yaml \
  /home/telmat/sec-xapp/deploy/ran/configs/gnb.yaml
```

Skip this step if Task 6 found `gnb.yaml` to be a trivial copy of `cots_n78_copied.yml` — note that finding in `deploy/ran/README.md` instead (Task 15).

- [ ] **Step 2: Fetch Open5GS config from the Core node**

```bash
mkdir -p /home/telmat/sec-xapp/deploy/core/config
scp -o StrictHostKeyChecking=no \
  "telmat@10.91.2.4:~/core/config/*.yaml" \
  /home/telmat/sec-xapp/deploy/core/config/
ls /home/telmat/sec-xapp/deploy/core/config/
```

- [ ] **Step 3: Strip `.log` files that may have been swept up by the wildcard**

```bash
rm -f /home/telmat/sec-xapp/deploy/core/config/*.log
```

- [ ] **Step 4: Record the upstream Open5GS fork commit**

```bash
cat > /home/telmat/sec-xapp/deploy/core/UPSTREAM_COMMIT.txt <<'EOF'
Open5GS is deployed via docker_open5gs, an unmodified checkout of:

  https://github.com/herlesupreeth/docker_open5gs
  commit 6531237 (Fix for session refresh)

No source-level patches. Only the YAML files in this directory
(deploy/core/config/) are locally authored — copy them into
~/core/config/ on the Core node's docker_open5gs checkout to
reproduce the deployed configuration.
EOF
```

- [ ] **Step 5: Scan fetched configs for secrets before committing**

```bash
grep -n "password\|secret\|PASS\|key" /home/telmat/sec-xapp/deploy/core/config/*.yaml /home/telmat/sec-xapp/deploy/ran/configs/*.yaml 2>/dev/null
```

If this finds anything beyond Open5GS's standard non-secret placeholder fields (e.g. `db_uri`, `key` fields that are actually AMF/SUCI protocol config, not credentials), flag it to the user before committing — Open5GS `amf.yaml`/`smf.yaml` sometimes embed subscriber test keys.

- [ ] **Step 6: Commit**

```bash
cd /home/telmat/sec-xapp
git add deploy/ran/configs/ deploy/core/config/ deploy/core/UPSTREAM_COMMIT.txt
git commit -m "feat(deploy): pull live RAN and Core node configs into repo"
```

---

## Task 14: Dataset manifest

**Files:**
- Create: `docs/DATASET_MANIFEST.md`
- Create: `scripts/data/fetch_dataset.sh`

- [ ] **Step 1: Generate SHA256 manifest of the CSV datasets currently on disk**

```bash
cd /home/telmat/sec-xapp
find csv/ -name "*.csv" -exec sha256sum {} \; > /tmp/dataset_checksums.txt
wc -l /tmp/dataset_checksums.txt
for f in csv/*.csv; do
  [ -e "$f" ] || continue
  rows=$(wc -l < "$f")
  echo "$f: $rows rows"
done
```

- [ ] **Step 2: Write the manifest document**

Using the `sha256sum` and row-count output from Step 1, write `docs/DATASET_MANIFEST.md` with a table: filename, rows, SHA256, and a one-line description per file (baseline/attack/validation — taken from the dataset descriptions already established in `docs/CLAUDE.md`'s "Dataset aktif" section and the Aug 7 investigation of the Juni datasets).

- [ ] **Step 3: Write the fetch script**

```bash
cat > scripts/data/fetch_dataset.sh <<'EOF'
#!/bin/bash
# Downloads the training/validation/attack CSV datasets and verifies
# them against docs/DATASET_MANIFEST.md.
#
# Usage: DATASET_URL=<release-url> ./scripts/data/fetch_dataset.sh
set -e

: "${DATASET_URL:?Set DATASET_URL to the dataset release archive URL (see docs/DATASET_MANIFEST.md)}"
DEST_DIR="$(dirname "$0")/../../csv"
mkdir -p "$DEST_DIR"

echo "[fetch_dataset] Downloading from $DATASET_URL ..."
curl -fL "$DATASET_URL" -o /tmp/sec-xapp-dataset.tar.gz

echo "[fetch_dataset] Extracting to $DEST_DIR ..."
tar -xzf /tmp/sec-xapp-dataset.tar.gz -C "$DEST_DIR"

echo "[fetch_dataset] Verifying checksums against docs/DATASET_MANIFEST.md ..."
cd "$DEST_DIR"
for f in *.csv; do
  expected=$(grep "$f" ../docs/DATASET_MANIFEST.md | grep -oE '[a-f0-9]{64}')
  [ -z "$expected" ] && { echo "  SKIP $f (not in manifest)"; continue; }
  actual=$(sha256sum "$f" | cut -d' ' -f1)
  if [ "$expected" = "$actual" ]; then
    echo "  OK   $f"
  else
    echo "  FAIL $f (checksum mismatch)"
    exit 1
  fi
done
echo "[fetch_dataset] Done."
EOF
chmod +x scripts/data/fetch_dataset.sh
```

- [ ] **Step 4: Verify the script is syntactically valid**

```bash
bash -n scripts/data/fetch_dataset.sh
```

- [ ] **Step 5: Commit**

```bash
git add docs/DATASET_MANIFEST.md scripts/data/fetch_dataset.sh
git commit -m "docs(dataset): add checksum manifest and fetch script; CSVs stay out of the repo"
```

`DATASET_URL` stays a user-filled config value — the actual release destination (Drive/Zenodo/etc.) is the user's call, made at publish time, not part of this plan.

---

## Task 15: Write `deploy/{ric,ran,core}/README.md`

**Files:**
- Modify: `deploy/ric/README.md` (create fully — no stub existed)
- Modify: `deploy/ran/README.md` (fill in the stub from Task 3)
- Modify: `deploy/core/README.md` (fill in the stub from Task 3)

- [ ] **Step 1: Write `deploy/ric/README.md`**

Content sourced from `docs/CLAUDE.md` (kept file) sections "Cara Menjalankan", "Hot-Label Switching", "Mitigasi C xApp": build command (`cd vendor/flexric/build && make -j$(nproc) xapp_sec_moni xapp_sec_mitigate`), how to run `deploy/ric/start_xapp_c.sh` / `start_xapp_c_mitigate.sh`, the `--mitigate` flag toggle table, and the hot-label file format (`/tmp/xapp_label`, `<label>,<scenario>,<attacker_ue>,<epoch_ms>`). Use `Write` to create the file with this content structured under headings `## Build`, `## Run`, `## Hot-label switching`, `## Mitigation modes`.

- [ ] **Step 2: Fill in `deploy/ran/README.md`**

Start from the `## Interfaces` and `## gNB config fields` sections already written in Task 3 Step 2. Add:
- `## Build` — srsRAN build command against `vendor/srsran`
- `## Config` — the Task 6 finding about `gnb.yaml` vs `cots_n78_copied.yml` (written verbatim from that task's recorded note, not re-derived)
- `## Run` — `./srsRAN_Project/build/apps/gnb/gnb -c configs/gnb.yaml` (confirmed command from the brainstorming investigation)

- [ ] **Step 3: Fill in `deploy/core/README.md`**

Start from the `UPSTREAM_COMMIT.txt` reference (Task 13). Add:
- `## Setup` — clone `docker_open5gs` at the pinned commit, copy `deploy/core/config/*.yaml` over the checkout's `config/` directory
- `## Slice management` — `change_subscriber_slice.sh` usage (from its existing header comment: `./change_subscriber_slice.sh <IMSI> <SST>`)
- `## Mitigation fallback` — SSH AMF barring commands (from `docs/CLAUDE.md` "Fallback: SSH AMF Barring")

- [ ] **Step 4: Commit**

```bash
cd /home/telmat/sec-xapp
git add deploy/ric/README.md deploy/ran/README.md deploy/core/README.md
git commit -m "docs(deploy): write per-node README covering build, run, and config for RIC/RAN/Core"
```

---

## Task 16: Write the root `README.md`; retire superseded docs

**Files:**
- Create: `README.md` (root)
- Remove: `docs/README.md` (superseded — describes the old single-LSTM v2 pipeline, pre-per-UE-model era)
- Remove: `docs/CLAUDE.md` (content folded into root README + deploy READMEs across Tasks 8-15)

- [ ] **Step 1: Confirm nothing else references `docs/CLAUDE.md` or `docs/README.md` before deleting them**

```bash
cd /home/telmat/sec-xapp
grep -rln "docs/CLAUDE.md\|docs/README.md" --include="*.md" --include="*.sh" --include="*.py" . 2>/dev/null | grep -v "^\./docs/CLAUDE.md$\|^\./docs/README.md$"
```

- [ ] **Step 2: Write `README.md`** covering, in order (per the approved design spec §5):

1. **Ringkasan proyek** — anomaly detection xApp, LSTM/GRU-Autoencoder + rule-based hybrid, physical 3-node O-RAN testbed.
2. **Topologi testbed** — table (Node/IP/Software/Path) sourced from `docs/CLAUDE.md`'s topology table, updated paths: RIC → `vendor/flexric/`, RAN → `vendor/srsran/` (build) + `deploy/ran/configs/` (config), Core → `deploy/core/`.
3. **Struktur repo** — table mapping every top-level directory (`src/`, `scripts/`, `models/`, `deploy/`, `observability/`, `docs/`, `vendor/`) to its purpose, referencing the design spec §3 tree.
4. **Dokumentasi kode** — per-module summary of `src/detection/feature_schema.py`, `scoring.py`, `gru_autoencoder.py`, `lstm_autoencoder.py`, `detector.py`, `feature_groups.py`; the 10-feature table from `docs/CLAUDE.md`.
5. **Cara reproduksi hasil** — `scripts/data/fetch_dataset.sh` → `scripts/train/train_gru_ue.py` / `train_lstm_ue.py` → `scripts/eval/evaluate_per_ue_v2.py` → `scripts/plot/plot_learning_curves_v5.py`, with the exact flags each script takes (read from each script's `argparse` block, not guessed).
6. **Cara deployment testbed** — `git clone --recursive`, then link to `deploy/ric/README.md`, `deploy/ran/README.md`, `deploy/core/README.md`; startup order (RIC → RAN → Core, from `docs/CLAUDE.md`'s "Cara Menjalankan").
7. **Skenario serangan & mitigasi** — table from `docs/CLAUDE.md`'s "Skenario Serangan" and "Mitigasi C xApp" sections; note that attack-orchestration scripts live in a separate controller repo (`~/xapp/security-scripts/`), not included here.
8. **Known issues** — table from `docs/CLAUDE.md`'s "Known Issues" section (DRB metrics always 0, CQI keep-last, srsRAN SIZE(0) MeasurementData, etc.).
9. **Lisensi & atribusi** — FlexRIC is MPL-2.0 (EURECOM Mosaic5G), srsRAN dual AGPLv3/commercial (SRS); both are submodules under `vendor/`, not relicensed by this repo.

Use `Write` to create `README.md` with all nine sections populated from the exact source material cited above — read each source file's current (post-restructure) content before writing the corresponding section, rather than reusing pre-restructure paths from memory.

- [ ] **Step 3: Remove superseded docs**

```bash
cd /home/telmat/sec-xapp
git rm docs/README.md docs/CLAUDE.md
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs: write root README covering code, reproduction, and 3-node deployment

Consolidates and retires docs/README.md (stale, described the pre-per-UE
single-LSTM v2 pipeline) and docs/CLAUDE.md (AI-assistant working notes,
folded into README.md and deploy/*/README.md).
EOF
)"
```

---

## Task 17: Final verification

**Files:** none.

- [ ] **Step 1: Confirm no literal credentials remain anywhere in the tracked tree**

```bash
cd /home/telmat/sec-xapp
git grep -n "sshpass\|PASS=\"123\"\|-p \"123\"\|password.*=.*['\"].*['\"]" -- . ':!docs/superpowers'
```

Expected: no output (or only false-positive matches in Open5GS YAML field names like `mme_gid`/`security` that aren't literal secrets — verify each hit manually).

- [ ] **Step 2: Confirm no broken gitlinks or unregistered submodules**

```bash
git ls-files -s | grep '^160000'
git submodule status
```

Expected: `git ls-files -s | grep 160000` shows exactly `vendor/flexric` and `vendor/srsran`, both matching entries in `git submodule status` with no `-`/`+` prefix.

- [ ] **Step 3: Test a fresh recursive clone and measure size**

```bash
cd /tmp
rm -rf sec-xapp-clone-test
git clone --recursive /home/telmat/sec-xapp -b repo-cleanup sec-xapp-clone-test
du -sh sec-xapp-clone-test
```

Expected: clone succeeds, both submodules populate, total size is well under the 300 MB target from the design spec's definition of done (excluding `csv/` dataset, which stays external).

- [ ] **Step 4: Confirm README claims match reality — spot-check one reproduction command**

```bash
cd /tmp/sec-xapp-clone-test
./venv/bin/python3 scripts/eval/verify_scoring_math.py --help 2>&1 | head -5 || echo "NOTE: venv not present in fresh clone (expected — venv/ is gitignored); verify with system python3 instead:"
python3 scripts/eval/verify_scoring_math.py --help 2>&1 | head -5
```

Expected: the script's argparse help prints, confirming the path documented in the README's "Cara reproduksi hasil" section is correct post-restructure.

- [ ] **Step 5: Clean up the test clone**

```bash
rm -rf /tmp/sec-xapp-clone-test
```

- [ ] **Step 6: Report to user** — summarize final `.git` size, working-tree size, submodule pin commits, and the two borderline doc decisions flagged in Task 3 (Step 6 note on `PRD_Security_xApp.md`) and Task 6 (gnb.yaml finding) for their final sign-off before merging `repo-cleanup` into `benign-calibrated-scoring-eval`/`master` or pushing to `5g-oran-testbed-itb/sec-xapp`.

**Merging back and pushing to the public org are NOT included as automatic steps in this plan** — per the safety principle of confirming before actions visible to others, that merge/push happens only after the user reviews the `repo-cleanup` branch's final commit log.

---

## Self-review notes

- **Spec coverage:** §2 (submodule treatment) → Tasks 11-12. §3 (directory structure) → Tasks 8-10, 13. §4 (data/model handling) → Task 7, 14. §5 (README contents) → Task 16. §6 (security) → Task 4. §7 (.gitignore) → Tasks 3, 5 (RAN node's own `.gitignore` fix from the earlier brainstorm is a manual note to the user, since it lives outside this repo — not automatable from here; flagged explicitly here rather than silently dropped). §8 (open questions) → Task 5 Step 5 (PDF), Task 6 (gnb.yaml), Task 7 (model scope). §9 (definition of done) → Task 17.
- **New finding folded in beyond the original spec:** the 149 uncommitted changes and the thesis-document curation requirement, both surfaced after spec approval — handled in Tasks 1 and 3, with the curation rule (specific patterns, not blanket `*.md`) implemented exactly as the user specified.
- **Placeholder check:** the only intentionally-open value is `DATASET_URL` in `scripts/data/fetch_dataset.sh` (Task 14) — that's a deliberate runtime config for the script's future user, not a gap in this plan's instructions.
