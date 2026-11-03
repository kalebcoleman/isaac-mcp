# Third-party notices

This repository ships **no assets**. The twin is designed to use the third-party assets
below, which each user keeps in their local asset folder (paths are relative to
`<asset-folder>/ur5e-dt/`; see `docs/ASSETS.md`). Older revisions of the repository carried
them; these notices apply to anyone who obtains or redistributes them. The
provenance audit is in [`docs/provenance/ASSETS.md`](docs/provenance/ASSETS.md).
NVIDIA Isaac Sim assets and the Vention workstation are **not** redistributed;
they are fetched from their official source or supplied by the operator.

## Husarion OnRobot RG2 description — Apache-2.0

Files:

- `<asset-folder>/ur5e-dt/adapter_ref/`
- `<asset-folder>/ur5e-dt/gripper/husarion_rg2/`
- `<asset-folder>/ur5e-dt/gripper/QuickChanger.usd`
- `<asset-folder>/ur5e-dt/gripper/configuration/QuickChanger_*.usd`

Source: <https://github.com/husarion/ur_onrobot_rg2_ros> (`onrobot_rg2_description`).

Copyright Husarion and contributors. Licensed under the Apache License, Version
2.0. A copy of the licence is at
[`docs/provenance/husarion-ur-onrobot-rg2-LICENSE`](docs/provenance/husarion-ur-onrobot-rg2-LICENSE).

Changes: the URDF/xacro sources were converted to USD with the Isaac Sim URDF
importer; visuals and colliders were reparented under the links; the
Quick-Changer was converted and reoriented for the flange mount. The files in
`adapter_ref/` are unmodified copies.

## FMB — Functional Manipulation Benchmark — CC BY 4.0

Files:

- `<asset-folder>/ur5e-dt/objects/fmb1/`
- `<asset-folder>/ur5e-dt/objects/fmb2/`
- `<asset-folder>/ur5e-dt/objects/fmb3/`
- `<asset-folder>/ur5e-dt/fmb_assembly1.json`
- `<asset-folder>/ur5e-dt/fmb_assembly2.json`
- `<asset-folder>/ur5e-dt/fmb_assembly3.json`

Source: *FMB: A Functional Manipulation Benchmark for Generalizable Robotic
Learning*, Jianlan Luo, Charles Xu, Xinyang Geng, Gilbert Feng, Kuan Fang,
Liam Tan, Stefan Schaal and Sergey Levine (UC Berkeley),
<https://functional-manipulation-benchmark.github.io/>.

The FMB project releases its CAD and data under the Creative Commons
Attribution 4.0 International License:
<https://creativecommons.org/licenses/by/4.0/>.

Changes: the object CAD was converted to USD; materials, colliders and physics
overlays were authored; some objects were renamed, rescaled or repositioned for
the UR5e workcell; ArUco poses were added. The `doc` metadata that named the
original workstation path was removed.

## Generated ArUco markers

`<asset-folder>/ur5e-dt/objects/aruco/` contains marker images generated from the
standard OpenCV 4x4 and 5x5 ArUco dictionaries, plus pose JSON produced by the
twin's tooling. OpenCV is Apache-2.0; the generated dictionary patterns are not
copyrightable.

## Assets that are not redistributed

- **NVIDIA Isaac Sim** — the UR5e robot, RealSense D455 model and NVIDIA
  material libraries remain under the NVIDIA Isaac Sim Additional Software and
  Materials License. `scripts/fetch_ur5e_assets.py` downloads them into the
  operator's cache, or the extension references NVIDIA's public asset root
  directly. They are not part of this repository.
- **Vention workstation** — the Vention MachineBuilder export is not
  redistributed. An operator may supply their own copy in the asset cache; the
  twin otherwise builds a simple placeholder bench.
- **Poliigon** — the `WoodVeneerOak_7760` textures were removed. Their licence
  does not permit embedding them in a redistributed scene.
