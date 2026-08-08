#!/bin/bash
# Downloads the curated training/validation/attack CSV datasets and verifies
# them against docs/DATASET_MANIFEST.md.
#
# Usage: DATASET_URL=<release-archive-url> ./scripts/data/fetch_dataset.sh
set -e

: "${DATASET_URL:?Set DATASET_URL to the dataset release archive URL (see docs/DATASET_MANIFEST.md)}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST_DIR="$REPO_ROOT/csv"
mkdir -p "$DEST_DIR"

echo "[fetch_dataset] Downloading from $DATASET_URL ..."
curl -fL "$DATASET_URL" -o /tmp/sec-xapp-dataset.tar.gz

echo "[fetch_dataset] Extracting to $DEST_DIR ..."
tar -xzf /tmp/sec-xapp-dataset.tar.gz -C "$DEST_DIR"

echo "[fetch_dataset] Verifying checksums against docs/DATASET_MANIFEST.md ..."
cd "$DEST_DIR"
status=0
for f in *.csv; do
  expected=$(grep -F "$f" "$REPO_ROOT/docs/DATASET_MANIFEST.md" | grep -oE '[a-f0-9]{64}')
  [ -z "$expected" ] && { echo "  SKIP $f (not in manifest)"; continue; }
  actual=$(sha256sum "$f" | cut -d' ' -f1)
  if [ "$expected" = "$actual" ]; then
    echo "  OK   $f"
  else
    echo "  FAIL $f (checksum mismatch)"
    status=1
  fi
done
[ "$status" -eq 0 ] || exit 1
echo "[fetch_dataset] Done."
