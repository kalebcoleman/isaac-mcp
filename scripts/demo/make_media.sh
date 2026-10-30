#!/usr/bin/env bash
# make_media.sh SRC_DIR DEST_DIR
set -euo pipefail

SRC_DIR="${1:-}"
DEST_DIR="${2:-}"
if [ -z "$SRC_DIR" ] || [ -z "$DEST_DIR" ]; then
    echo "Usage: $0 SRC_DIR DEST_DIR" >&2
    exit 2
fi

FFMPEG="${FFMPEG:-}"
if [ -z "$FFMPEG" ]; then
    if [ -x "$HOME/.local/bin/ffmpeg" ]; then FFMPEG="$HOME/.local/bin/ffmpeg";
    elif command -v ffmpeg >/dev/null 2>&1; then FFMPEG="$(command -v ffmpeg)";
    else echo "ffmpeg not found" >&2; exit 1; fi
fi
FFPROBE=""
if [ -x "$HOME/.local/bin/ffprobe" ]; then FFPROBE="$HOME/.local/bin/ffprobe";
elif command -v ffprobe >/dev/null 2>&1; then FFPROBE="$(command -v ffprobe)"; fi

mkdir -p "$DEST_DIR"
LIMIT_PNG=1258291   # ~1.2 MB
LIMIT_MP4=10485760  # 10 MB

total_size=0

# helpers for duration/resolution
get_duration() {
    local f="$1"
    if [ -n "$FFPROBE" ]; then
        "$FFPROBE" -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$f" 2>/dev/null | head -1 || true
    else
        "$FFMPEG" -i "$f" 2>&1 | grep -oE 'Duration: [0-9:.]+' | head -1 | sed 's/Duration: //' || true
    fi
}

get_resolution() {
    local f="$1"
    if [ -n "$FFPROBE" ]; then
        "$FFPROBE" -v error -select_streams v:0 -show_entries stream=width,height -of csv=s=x:p=0 "$f" 2>/dev/null | head -1 || true
    else
        "$FFMPEG" -i "$f" 2>&1 | sed -n 's/.*, \([0-9][0-9]*x[0-9][0-9]*\).*/\1/p' | head -1 || true
    fi
}

file_size() { stat -c%s "$1"; }

compress_png() {
    local in="$1" outdir="$2" base stem out
    base="$(basename "$in")"
    stem="${base%.*}"
    out="$outdir/${stem}.png"
    # attempt 1: scale + png compression
    "$FFMPEG" -y -i "$in" -vf "scale='min(1920,iw)':-2" -compression_level 9 "$out" >/dev/null 2>&1 || true
    if [ ! -f "$out" ]; then
        cp "$in" "$out"
    fi
    if [ "$(file_size "$out")" -le "$LIMIT_PNG" ]; then
        echo "$out"; return
    fi
    # attempt 2: palette quantize
    "$FFMPEG" -y -i "$in" -vf "scale='min(1920,iw)':-2,format=pal8" -compression_level 9 "$out" >/dev/null 2>&1 || true
    if [ -f "$out" ] && [ "$(file_size "$out")" -le "$LIMIT_PNG" ]; then
        echo "$out"; return
    fi
    # attempt 3: stronger via png with pred (try qscale not applicable); retry scale 1280
    "$FFMPEG" -y -i "$in" -vf "scale='min(1280,iw)':-2" -compression_level 9 "$out" >/dev/null 2>&1 || true
    if [ -f "$out" ] && [ "$(file_size "$out")" -le "$LIMIT_PNG" ]; then
        echo "$out"; return
    fi
    # last resort: high-quality jpg, keep stem; reduce quality/scale until within limit
    local jpg="$outdir/${stem}.jpg"
    local q=2 scale=1920
    while :; do
        "$FFMPEG" -y -i "$in" -vf "scale='min(${scale},iw)':-2" -q:v "$q" "$jpg" >/dev/null 2>&1 || true
        if [ -f "$jpg" ] && [ "$(file_size "$jpg")" -le "$LIMIT_PNG" ]; then break; fi
        if [ "$q" -lt 10 ]; then q=$((q+3));
        elif [ "$scale" -gt 960 ]; then scale=960; q=5;
        else break; fi
    done
    if [ ! -f "$jpg" ]; then cp "$in" "$jpg"; fi
    rm -f "$out"
    echo "$jpg"
}

compress_mp4() {
    local in="$1" outdir="$2" base stem out crf size
    base="$(basename "$in")"
    stem="${base%.*}"
    out="$outdir/${stem}.mp4"
    crf=28
    while :; do
        "$FFMPEG" -y -i "$in" -vf "scale=-2:720" -c:v libx264 -crf "$crf" -preset slow -movflags +faststart -an "$out" >/dev/null 2>&1 || true
        if [ ! -f "$out" ]; then break; fi
        size=$(file_size "$out")
        if [ "$size" -le "$LIMIT_MP4" ]; then break; fi
        crf=$((crf+2))
        if [ "$crf" -gt 51 ]; then break; fi
    done
    echo "$out"
}

# Collect inputs
pngs=()
if [ -d "$SRC_DIR/shots" ]; then
    while IFS= read -r -d '' f; do pngs+=("$f"); done < <(find "$SRC_DIR/shots" -type f -name '*.png' -print0)
fi
if [ -d "$SRC_DIR/data" ]; then
    while IFS= read -r -d '' f; do pngs+=("$f"); done < <(find "$SRC_DIR/data" -maxdepth 1 -type f -name '*.png' -print0)
fi
mp4s=()
if [ -d "$SRC_DIR/videos" ]; then
    while IFS= read -r -d '' f; do mp4s+=("$f"); done < <(find "$SRC_DIR/videos" -type f -name '*.mp4' -print0)
fi

produced=()
for f in "${pngs[@]:-}"; do
    [ -z "$f" ] && continue
    out=$(compress_png "$f" "$DEST_DIR")
    produced+=("$out")
done
for f in "${mp4s[@]:-}"; do
    [ -z "$f" ] && continue
    out=$(compress_mp4 "$f" "$DEST_DIR")
    produced+=("$out")
done

# INDEX.md
index="$DEST_DIR/INDEX.md"
{
    echo "# Media index"
    echo ""
    echo "Source: $SRC_DIR"
    echo ""
    for f in "${produced[@]}"; do
        sz=$(file_size "$f")
        rel=$(basename "$f")
        if [[ "$f" == *.mp4 ]]; then
            dur=$(get_duration "$f")
            res=$(get_resolution "$f")
            # normalize duration to seconds if HH:MM:SS
        if echo "$dur" | grep -q ':'; then
            IFS=: read -r h m s <<< "$dur"
            dur=$(awk -v h="$h" -v m="$m" -v s="$s" 'BEGIN{printf "%.2f", h*3600+m*60+s}')
        fi
        echo "- \`$rel\` — ${sz} bytes, duration ${dur}s, resolution ${res}"
        else
            echo "- \`$rel\` — ${sz} bytes"
        fi
        total_size=$((total_size + sz))
    done
    echo ""
    echo "Total: ${total_size} bytes"
} > "$index"

echo "Produced ${#produced[@]} files. Total ${total_size} bytes."
echo "Index: $index"