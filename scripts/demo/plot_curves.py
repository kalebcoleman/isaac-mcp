#!/usr/bin/env python3
"""Plot TensorBoard training curves into slide-ready PNGs.

Usage:
    python plot_curves.py RUN_DIR OUT_DIR [--prefix S7]
"""
import argparse
import glob
import json
import os
import re
import sys

# Prefer matplotlib; fall back to PIL/numpy-only writer if missing.
try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    HAS_MPL = True
except Exception:
    HAS_MPL = False

try:
    import numpy as np

    HAS_NUMPY = True
except Exception:
    HAS_NUMPY = False

try:
    from PIL import Image, ImageDraw, ImageFont

    HAS_PIL = True
except Exception:
    HAS_PIL = False

from tensorboard.backend.event_processing.event_accumulator import EventAccumulator


def find_event_dirs(run_dir):
    files = []
    for root, _, names in os.walk(run_dir):
        for n in names:
            if n.startswith("events.out.tfevents."):
                files.append(os.path.join(root, n))
    dirs = []
    seen = set()
    for f in files:
        d = os.path.dirname(f)
        if d not in seen:
            seen.add(d)
            dirs.append(d)
    # also try run_dir and run_dir/summaries directly
    for d in (run_dir, os.path.join(run_dir, "summaries")):
        if d not in seen and os.path.isdir(d):
            seen.add(d)
            dirs.append(d)
    return dirs, files


def load_accumulator(run_dir):
    dirs, files = find_event_dirs(run_dir)
    if not files:
        raise RuntimeError(f"No events.out.tfevents.* found under {run_dir}")
    best = None
    best_ntags = -1
    for d in dirs:
        try:
            ea = EventAccumulator(d, size_guidance={"scalars": 0})
            ea.Reload()
            tags = ea.Tags().get("scalars", [])
            if len(tags) > best_ntags:
                best_ntags = len(tags)
                best = (ea, tags, d)
        except Exception as e:
            print(f"[plot_curves] could not read {d}: {e}", file=sys.stderr)
    if best is None:
        raise RuntimeError("Could not read any TensorBoard event files")
    ea, tags, d = best
    print(f"[plot_curves] using event dir: {d}")
    print(f"[plot_curves] available scalar tags ({len(tags)}):")
    for t in tags:
        print(f"   {t}")
    return ea, tags


def task_name_from_path(run_dir):
    run_dir = os.path.abspath(run_dir)
    name = os.path.basename(run_dir.rstrip(os.sep))
    # timestamp-like dir -> use parent (experiment) name
    if re.match(r"^\d{4}[-_]", name):
        parent = os.path.basename(os.path.dirname(run_dir.rstrip(os.sep)))
        if parent and parent not in ("", "/", "rl_games", "logs"):
            name = parent
    # try run.json for a nicer task name but spec says path; keep path-derived
    name = name.replace("_", " ").replace("-", " ").strip()
    # title case
    acronyms = {"ur5e": "UR5e", "ur10": "UR10", "ur10e": "UR10e", "rl": "RL", "ppo": "PPO"}
    name = " ".join(acronyms.get(w.lower(), w.capitalize()) for w in name.split())
    return name


def pick_tag(tags, candidates_exact, predicate=None):
    tset = set(tags)
    for c in candidates_exact:
        if c in tset:
            return c
    # predicate search
    matches = []
    for t in tags:
        ok = False
        if predicate is not None:
            ok = predicate(t)
        matches.append((t, ok))
    filtered = [t for t, ok in matches if ok]
    if filtered:
        filtered.sort()
        return filtered[0]
    return None


def scalar_series(ea, tag):
    events = ea.Scalars(tag)
    xs = [e.step for e in events]
    ys = [e.value for e in events]
    return xs, ys


def smooth(ys, window=None):
    if not HAS_NUMPY:
        return list(ys)
    arr = np.asarray(ys, dtype=float)
    n = len(arr)
    if n < 3:
        return list(arr)
    if window is None:
        window = max(3, n // 20)
    if window % 2 == 0:
        window += 1
    if window > n:
        window = n if n % 2 == 1 else n - 1
        if window < 1:
            window = 1
    if window <= 1:
        return list(arr)
    kernel = np.ones(window) / window
    # pad
    pad = window // 2
    padded = np.pad(arr, (pad, pad), mode="edge")
    out = np.convolve(padded, kernel, mode="valid")
    # trim to n
    if len(out) > n:
        start = (len(out) - n) // 2
        out = out[start : start + n]
    elif len(out) < n:
        out = np.resize(out, n)
    return list(out)


def stats(ys):
    if not ys:
        return {}
    if HAS_NUMPY:
        arr = np.asarray(ys, dtype=float)
        return {
            "first": float(arr[0]),
            "mid": float(arr[len(arr) // 2]),
            "last": float(arr[-1]),
            "min": float(arr.min()),
            "max": float(arr.max()),
            "count": int(len(arr)),
        }
    return {
        "first": float(ys[0]),
        "mid": float(ys[len(ys) // 2]),
        "last": float(ys[-1]),
        "min": float(min(ys)),
        "max": float(max(ys)),
        "count": int(len(ys)),
    }


METRIC_LABELS = {
    "reward": ("Reward", "Reward"),
    "success": ("Success rate", "Success rate"),
    "distance": ("Position error (m)", "Position error"),
    "fps": ("Steps/s", "Step FPS"),
}

FILE_NAMES = {
    "reward": "{prefix}-01-reward-curve.png",
    "success": "{prefix}-02-success-curve.png",
    "distance": "{prefix}-03-distance-curve.png",
    "fps": "{prefix}-04-fps.png",
}


def plot_one(xs, ys, title, ylabel, out_path, color="#1f77b4"):
    if not HAS_MPL:
        # PIL fallback: write a simple line chart PNG
        return plot_one_pil(xs, ys, title, ylabel, out_path, color)
    fig, ax = plt.subplots(figsize=(16, 9), dpi=100)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#fafafa")
    # raw
    ax.plot(xs, ys, color=color, alpha=0.25, linewidth=1.0, label="raw")
    sm = smooth(ys)
    ax.plot(xs, sm, color=color, linewidth=2.5, label="smoothed")
    ax.set_title(title, fontsize=24, fontweight="bold", pad=16)
    ax.set_xlabel("Iteration", fontsize=18)
    ax.set_ylabel(ylabel, fontsize=18)
    ax.tick_params(axis="both", labelsize=14)
    ax.grid(True, alpha=0.3, linestyle="--")
    # annotate final value
    if len(xs) > 0:
        ax.annotate(
            f"final: {ys[-1]:.4g}",
            xy=(xs[-1], ys[-1]),  # label and arrow both at the raw last value
            xytext=(-80, 20),
            textcoords="offset points",
            fontsize=14,
            color=color,
            arrowprops=dict(arrowstyle="->", color=color, lw=1.2),
        )
    ax.legend(fontsize=14, loc="best")
    fig.tight_layout()
    fig.savefig(out_path, dpi=100)
    plt.close(fig)
    return True


def plot_one_pil(xs, ys, title, ylabel, out_path, color="#1f77b4"):
    # Minimal PNG writer using PIL if matplotlib missing.
    if not (HAS_PIL and HAS_NUMPY):
        # SVG fallback
        return plot_one_svg(xs, ys, title, ylabel, out_path)
    W, H = 1600, 900
    img = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    # margins
    ml, mr, mt, mb = 100, 60, 80, 80
    pw = W - ml - mr
    ph = H - mt - mb
    # axes
    draw.rectangle([ml, mt, ml + pw, mt + ph], outline=(200, 200, 200))
    if not xs or not ys:
        img.save(out_path)
        return True
    xs = [float(v) for v in xs]
    ys = [float(v) for v in ys]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if xmax == xmin:
        xmax = xmin + 1
    if ymax == ymin:
        ymax = ymin + 1
    pad_y = (ymax - ymin) * 0.05
    ymin -= pad_y
    ymax += pad_y

    def px(x):
        return int(ml + (x - xmin) / (xmax - xmin) * pw)

    def py(y):
        return int(mt + ph - (y - ymin) / (ymax - ymin) * ph)

    # raw line
    pts = [(px(x), py(y)) for x, y in zip(xs, ys)]
    for i in range(len(pts) - 1):
        draw.line([pts[i], pts[i + 1]], fill=(180, 180, 220), width=1)
    sm = smooth(ys)
    spts = [(px(x), py(y)) for x, y in zip(xs, sm)]
    for i in range(len(spts) - 1):
        draw.line([spts[i], spts[i + 1]], fill=(31, 119, 180), width=3)
    # title
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
        font2 = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
    except Exception:
        font = ImageFont.load_default()
        font2 = font
    draw.text((ml, 20), title, fill=(0, 0, 0), font=font)
    draw.text((ml, H - 40), "Iteration", fill=(0, 0, 0), font=font2)
    # y label vertical (approx horizontal)
    draw.text((10, mt), ylabel, fill=(0, 0, 0), font=font2)
    # annotate final
    if pts:
        draw.text((pts[-1][0] - 90, pts[-1][1] - 30), f"final: {ys[-1]:.4g}", fill=(31, 119, 180), font=font2)
    img.save(out_path)
    return True


def plot_one_svg(xs, ys, title, ylabel, out_path):
    # Write an SVG if nothing else available; rename path to .svg? Keep requested name but write svg content (rare).
    # We'll still write to out_path as SVG text (best effort).
    W, H = 1600, 900
    ml, mr, mt, mb = 100, 60, 80, 80
    pw = W - ml - mr
    ph = H - mt - mb
    if not xs:
        xs, ys = [], []
    xs = [float(v) for v in xs]
    ys = [float(v) for v in ys]
    if xs:
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
    else:
        xmin, xmax, ymin, ymax = 0, 1, 0, 1
    if xmax == xmin:
        xmax = xmin + 1
    if ymax == ymin:
        ymax = ymin + 1
    pad = (ymax - ymin) * 0.05
    ymin -= pad
    ymax += pad

    def px(x):
        return ml + (x - xmin) / (xmax - xmin) * pw

    def py(y):
        return mt + ph - (y - ymin) / (ymax - ymin) * ph

    lines = []
    lines.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}">')
    lines.append('<rect width="100%" height="100%" fill="white"/>')
    lines.append(f'<text x="{ml}" y="40" font-size="28" font-weight="bold">{title}</text>')
    if xs:
        raw_pts = " ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in zip(xs, ys))
        lines.append(f'<polyline points="{raw_pts}" fill="none" stroke="#b0c4de" stroke-width="1" opacity="0.4"/>')
        sm = smooth(ys)
        sm_pts = " ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in zip(xs, sm))
        lines.append(f'<polyline points="{sm_pts}" fill="none" stroke="#1f77b4" stroke-width="3"/>')
        lines.append(f'<text x="{px(xs[-1]) - 80}" y="{py(sm[-1]) - 10}" font-size="16" fill="#1f77b4">final: {ys[-1]:.4g}</text>')
    lines.append(f'<text x="{W/2}" y="{H-20}" font-size="18" text-anchor="middle">Iteration</text>')
    lines.append(f'<text x="20" y="{H/2}" font-size="18" transform="rotate(-90,20,{H/2})">{ylabel}</text>')
    lines.append("</svg>")
    with open(out_path, "w") as fh:
        fh.write("\n".join(lines))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--prefix", default="S7")
    args = ap.parse_args()

    run_dir = os.path.expanduser(args.run_dir)
    out_dir = os.path.expanduser(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    ea, tags = load_accumulator(run_dir)
    task_name = task_name_from_path(run_dir)

    # reward tag
    reward_tag = pick_tag(tags, ["rewards/iter"], lambda t: (t.startswith("rewards/") or t.startswith("Episode/")) and "reward" in t.lower())
    success_tag = pick_tag(tags, ["Episode/success_rate", "Episode/Metrics/success_rate"], None)
    # exact only for success (already handled by candidates); also allow contains
    if success_tag is None:
        for t in tags:
            if "success" in t.lower():
                success_tag = t
                break
    distance_tag = pick_tag(tags, ["Episode/dist_m", "Episode/Metrics/ee_pose/position_error"], None)
    if distance_tag is None:
        for t in tags:
            low = t.lower()
            if ("dist" in low) or ("position_error" in low) or ("position error" in low):
                distance_tag = t
                break
    fps_tag = pick_tag(tags, ["performance/step_fps"], None)
    if fps_tag is None:
        for t in tags:
            if "fps" in t.lower():
                fps_tag = t
                break

    print(f"[plot_curves] reward tag: {reward_tag}")
    print(f"[plot_curves] success tag: {success_tag}")
    print(f"[plot_curves] distance tag: {distance_tag}")
    print(f"[plot_curves] fps tag: {fps_tag}")

    metrics = [
        ("reward", reward_tag, "reward"),
        ("success", success_tag, "success"),
        ("distance", distance_tag, "distance"),
        ("fps", fps_tag, "fps"),
    ]

    curves = {}
    for key, tag, _ in metrics:
        if tag is None:
            if key == "success":
                print(f"[plot_curves] skipping {key}: tag absent (noted)")
            else:
                print(f"[plot_curves] WARNING: {key} tag absent; skipping")
            continue
        xs, ys = scalar_series(ea, tag)
        if not xs:
            print(f"[plot_curves] {tag}: no data; skipping")
            continue
        label, ylab = METRIC_LABELS[key][1], METRIC_LABELS[key][0]
        title = f"{task_name} — {label}"
        out_name = FILE_NAMES[key].format(prefix=args.prefix)
        out_path = os.path.join(out_dir, out_name)
        plot_one(xs, ys, title, ylab, out_path)
        print(f"[plot_curves] wrote {out_path} ({len(xs)} points)")
        curves[tag] = stats(ys)

    # write curves.json
    curves_path = os.path.join(out_dir, "curves.json")
    with open(curves_path, "w") as fh:
        json.dump(curves, fh, indent=2)
    print(f"[plot_curves] wrote {curves_path}")

    # skip note for success if absent
    if success_tag is None:
        note_path = os.path.join(out_dir, f"{args.prefix}-02-success-curve.SKIPPED.txt")
        with open(note_path, "w") as fh:
            fh.write("success curve skipped: no success_rate tag found in this run.\n")
        print(f"[plot_curves] noted skip: {note_path}")


if __name__ == "__main__":
    main()
