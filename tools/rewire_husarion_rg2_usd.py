#!/usr/bin/env python3
"""rewire_husarion_rg2_usd.py — repair the Isaac-URDF-importer's orphaned-visuals output.

The Isaac 5.1 URDF importer (scripts/convert_husarion_rg2_urdf.py) emits the standard 5-layer
"asset structure" (robot.usd + configuration/{base,physics,sensor}.usd). For the husarion RG2 it
left the geometry+materials ORPHANED: the 26 visual/collider Mesh prims and 32 Materials live in
TOP-LEVEL sibling scopes (/visuals/<link>/<part>/Scene, /colliders/<link>/...), while each
/onrobot_rg2/<link>/{visuals,collisions} prim is EMPTY with a non-resolving reference into the
sibling scope (the "Unresolved reference prim path .../physics.usd@</visuals/tool0>" warnings).
Because the twin references the asset by its defaultPrim (/onrobot_rg2), the sibling scopes are
never composed in at all -> the live gripper loads as a geometry-less, material-less SKELETON.

This tool RE-WIRES it (no re-author, no stripping): for every link it Sdf.CopySpec's the whole
/visuals/<link> and /colliders/<link> subtrees UNDER /onrobot_rg2/<link>/{visuals,collisions}
(mesh + Looks move together so material bindings stay valid), drops the orphaned top-level scopes,
and flattens to ONE self-contained USD with defaultPrim=onrobot_rg2.

Transform correctness: the orphaned subtrees already carry the correct per-link URDF visual origins
at the /visuals/<link>/<part> level (left/base = identity; the 3 right-side parts = pi-about-X +
z=-0.02 for parallel-jaw symmetry; quick_changer = compound + z=+0.015). CopySpec of the whole
subtree carries those origins intact (verified: post-reparent local-in-link xforms match the URDF
table exactly). So gap/depth/arc geometry (gap 0.21mm, depth 0.01mm, R=55.0) is preserved.

Usage:
    ~/env_isaaclab/bin/python scripts/rewire_husarion_rg2_usd.py \
        --src exts/ur5e-dt/assets/gripper/husarion_rg2/configuration/husarion_rg2_physics.usd \
        --out exts/ur5e-dt/assets/gripper/husarion_rg2/husarion_rg2_fixed.usd

physics.usd is the richest single layer (it carries /onrobot_rg2 + the joints + the /visuals and
/colliders sibling scopes together), so flattening IT resolves the internal absolute refs.
Runs its own headless Isaac (no MCP socket).
"""
import argparse
import os
from isaacsim import SimulationApp

app = SimulationApp({"headless": True})
from pxr import Usd, UsdGeom, UsdPhysics, Sdf  # noqa: E402


def child_names(layer, path):
    spec = layer.GetPrimAtPath(path)
    return [c.name for c in spec.nameChildren] if spec else []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="source layer to flatten (use the physics.usd layer)")
    ap.add_argument("--out", required=True, help="destination self-contained USD")
    ap.add_argument("--root", default="/onrobot_rg2", help="articulation root prim path")
    args = ap.parse_args()

    stage = Usd.Stage.Open(os.path.abspath(args.src))
    flat = stage.Flatten()  # one Sdf.Layer with /onrobot_rg2 + /visuals + /colliders + /meshes

    copied = []
    for scope, dst_name in (("/visuals", "visuals"), ("/colliders", "collisions")):
        for ln in child_names(flat, scope):
            link_path = f"{args.root}/{ln}"
            if not flat.GetPrimAtPath(link_path):
                continue  # /visuals or /colliders entry without a matching link (e.g. collision groups)
            dst = f"{link_path}/{dst_name}"
            Sdf.CreatePrimInLayer(flat, dst)
            ok = Sdf.CopySpec(flat, f"{scope}/{ln}", flat, dst)
            copied.append((dst, ok))

    # drop the now-redundant orphaned top-level scopes (content is under the links now).
    # keep /meshes in case instanceable subtrees reference it.
    for scope in ("visuals", "colliders"):
        if flat.GetPrimAtPath("/" + scope):
            del flat.rootPrims[scope]

    flat.defaultPrim = args.root.lstrip("/")
    flat.Export(os.path.abspath(args.out))

    # verify
    fs = Usd.Stage.Open(os.path.abspath(args.out))
    under = [p.GetPath().pathString for p in fs.Traverse()
             if p.GetTypeName() == "Mesh" and p.GetPath().pathString.startswith(args.root + "/")]
    mats = [p for p in fs.Traverse() if p.GetTypeName() == "Material"]
    art = [p.GetPath().pathString for p in fs.Traverse() if p.HasAPI(UsdPhysics.ArticulationRootAPI)]
    mim = [p.GetName() for p in fs.Traverse() for s in p.GetAppliedSchemas() if "Mimic" in s]
    print(f"CopySpec ops: {len(copied)} all_ok={all(o for _, o in copied)}")
    print(f"defaultPrim={fs.GetDefaultPrim().GetName()} articulationRoot={art} mimic={len(mim)}")
    print(f"Mesh prims under {args.root}/*: {len(under)} ; Materials: {len(mats)}")
    print(f"wrote {args.out}")
    app.close()


if __name__ == "__main__":
    main()
