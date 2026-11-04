#!/usr/bin/env python3
"""Exit 1 when the verifier's reused checkpoint has references to files that no longer exist.

A checkpoint is git-ignored and refers to assets by relative path, so one written before assets moved
reopens with an empty robot and fails much later, in the motion check. Reads text only; needs no
simulator. A missing checkpoint, or a binary layer it cannot read as text, passes: it is exported
fresh or left to USD. It never edits or moves the file.

    python3 scripts/check_checkpoint_refs.py checkpoints/ur5e-dt-workstation-person-view-portable.json
"""
import json
from pathlib import Path
import re
import sys

ARCS = re.compile(r"\b(?:references|payload)\s*=\s*(\[[^\]]*\]|<[^>]*>|@[^@\n]*@)")
ASSET = re.compile(r"@([^@\n]*)@")


def dead_references(checkpoint):
    """Return the local files an ASCII USD layer composes that do not exist."""
    try:
        text = checkpoint.read_text()
    except (OSError, UnicodeDecodeError):
        return []
    dead = []
    for arc in ARCS.findall(text):
        for asset in ASSET.findall(arc):
            if asset and "://" not in asset and not (checkpoint.parent / asset).exists():
                dead.append(asset)
    return sorted(set(dead))


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    manifest = Path(argv[1])
    checkpoint = manifest.parent / json.loads(manifest.read_text())["checkpoint"]
    if not checkpoint.is_file():
        return 0
    dead = dead_references(checkpoint)
    if not dead:
        return 0
    print(f"{checkpoint} has {len(dead)} reference(s) to missing files, for example {dead[0]}")
    print("It was probably written before your assets moved. Inspect it, move it aside and re-run;")
    print("the verifier then exports a new one from your current assets.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
