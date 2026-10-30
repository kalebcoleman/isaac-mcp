#!/usr/bin/env bash
# export_checkpoints.sh DEST_DIR [LOGS_ROOT ...]
set -euo pipefail

DEST_DIR="${1:-}"
if [ -z "$DEST_DIR" ]; then
    echo "Usage: $0 DEST_DIR [LOGS_ROOT ...]" >&2
    exit 2
fi
if [ ! -d "$DEST_DIR" ]; then
    echo "error: DEST_DIR must be an existing directory: $DEST_DIR" >&2
    exit 1
fi

shift || true
if [ "$#" -gt 0 ]; then
    ROOTS=("$@")
else
    ROOTS=(
        "$HOME/Desktop/ur5e-digital-twin/learning6/IsaacLab/logs/rl_games"
        "$HOME/Desktop/ur5e-digital-twin/learning6/logs/rl_games"
    )
fi

# collect run dirs containing nn/*.pth
RUNS=()
for root in "${ROOTS[@]}"; do
    root="${root/#\~/$HOME}"
    [ -d "$root" ] || continue
    while IFS= read -r -d '' pth; do
        nn_dir="$(dirname "$pth")"
        run_dir="$(dirname "$nn_dir")"
        # unique
        if [[ ! " ${RUNS[*]} " =~ " $run_dir " ]]; then
            RUNS+=("$run_dir")
        fi
    done < <(find "$root" -type f -path '*/nn/*.pth' -print0 2>/dev/null)
done

needed=0
for run in "${RUNS[@]:-}"; do
    [ -z "$run" ] && continue
    task="$(basename "$(dirname "$run")")"
    ts="$(basename "$run")"
    dest="$DEST_DIR/$task/$ts"
    mkdir -p "$dest"
    # newest pth
    newest="$(find "$run/nn" -maxdepth 1 -type f -name '*.pth' -printf '%T@ %p\n' 2>/dev/null | sort -t' ' -k1,1rn -k2,2r | head -1 | cut -d' ' -f2-)"
    if [ -n "$newest" ]; then
        mkdir -p "$dest/nn"
        cp -p "$newest" "$dest/nn/"
    fi
    if [ -d "$run/params" ]; then
        cp -a "$run/params" "$dest/"
    fi
    if [ -d "$run/summaries" ]; then
        cp -a "$run/summaries" "$dest/"
    fi
    if [ -f "$run/run.json" ]; then
        cp -p "$run/run.json" "$dest/"
    fi
done

# compute needed size (sum of files in DEST_DIR now? better sum of copied run files)
# We'll compute total size of DEST_DIR contents added: use du on DEST_DIR (approx). Instead compute copied bytes via find.
needed=$(find "$DEST_DIR" -type f -printf '%s\n' 2>/dev/null | awk '{s+=$1} END{print s+0}')

# MANIFEST.txt
manifest="$DEST_DIR/MANIFEST.txt"
{
    echo "# host: $(hostname)"
    echo "# date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# needed_bytes: $needed"
    # standard sha256 lines (relative paths)
    ( cd "$DEST_DIR" && find . -type f ! -name MANIFEST.txt -print0 | sort -z | xargs -0 sha256sum ) | sed 's|\./||'
} > "$manifest"
# Add sizes as comments per file (append a size section)
{
    echo ""
    echo "# sizes:"
    ( cd "$DEST_DIR" && find . -type f ! -name MANIFEST.txt -print0 | sort -z | while IFS= read -r -d '' f; do
        rel="${f#./}"
        sz=$(stat -c%s "$f")
        echo "# size=$sz $rel"
    done )
} >> "$manifest"

total=$(find "$DEST_DIR" -type f -printf '%s\n' | awk '{s+=$1} END{print s+0}')
echo "Exported ${#RUNS[@]} run(s). Total size: $total bytes."
echo "Manifest: $manifest"

# free space check
free=$(df -B1 --output=avail "$DEST_DIR" | tail -1 | tr -d ' ')
if [ "$((free))" -lt "$((needed * 2))" ]; then
    echo "WARNING: DEST_DIR filesystem free space ($free bytes) is less than 2x needed ($((needed*2)) bytes)." >&2
fi