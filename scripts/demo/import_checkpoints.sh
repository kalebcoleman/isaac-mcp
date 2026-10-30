#!/usr/bin/env bash
# import_checkpoints.sh SRC_DIR [DEST_LOGS_ROOT]
set -euo pipefail

SRC_DIR="${1:-}"
if [ -z "$SRC_DIR" ]; then
    echo "Usage: $0 SRC_DIR [DEST_LOGS_ROOT]" >&2
    exit 2
fi
if [ ! -d "$SRC_DIR" ]; then
    echo "error: SRC_DIR must be an existing directory: $SRC_DIR" >&2
    exit 1
fi
DEST_LOGS_ROOT="${2:-$HOME/Desktop/ur5e-digital-twin/learning6/IsaacLab/logs/rl_games}"
DEST_LOGS_ROOT="${DEST_LOGS_ROOT/#\~/$HOME}"

# verify manifest if present
manifest="$SRC_DIR/MANIFEST.txt"
if [ -f "$manifest" ]; then
    echo "Verifying MANIFEST.txt checksums..."
    ( cd "$SRC_DIR" && grep -v '^#' "$manifest" | grep -v '^[[:space:]]*$' | sha256sum -c - ) || { echo "Checksum verification FAILED" >&2; exit 1; }
    echo "Checksums OK."
else
    echo "WARNING: no MANIFEST.txt found; skipping verification" >&2
fi

mkdir -p "$DEST_LOGS_ROOT"

added=0
skipped=0
# find run dirs in SRC
RUNS=()
while IFS= read -r -d '' pth; do
    nn_dir="$(dirname "$pth")"
    run_dir="$(dirname "$nn_dir")"
    if [[ ! " ${RUNS[*]} " =~ " $run_dir " ]]; then
        RUNS+=("$run_dir")
    fi
done < <(find "$SRC_DIR" -type f -path '*/nn/*.pth' -print0 2>/dev/null)

for run in "${RUNS[@]:-}"; do
    [ -z "$run" ] && continue
    # relative run path from SRC_DIR
    rel="${run#$SRC_DIR/}"
    dest="$DEST_LOGS_ROOT/$rel"
    # copy each file, skip existing
    while IFS= read -r -d '' f; do
        frel="${f#$run/}"
        dfile="$dest/$frel"
        if [ -e "$dfile" ]; then
            echo "skip (exists): $dest/$frel"
            skipped=$((skipped+1))
        else
            mkdir -p "$(dirname "$dfile")"
            cp -p "$f" "$dfile"
            echo "added: $dest/$frel"
            added=$((added+1))
        fi
    done < <(find "$run" -type f -print0)
done

echo "Import complete: added $added file(s), skipped $skipped existing file(s)."