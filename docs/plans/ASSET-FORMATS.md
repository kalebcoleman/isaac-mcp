# Asset formats: what we import and export, and what is missing

Goal (Kaleb, 2026-11-05): be close to universal about getting a robot or object from the internet into a scene, driving it, and getting it back out,
without ever running code from a download. This page is the research behind that goal and the order to build it in. It was written from what Isaac Sim 6.2
ships (the extension list in its install) and NVIDIA's own pages; items marked *tested* were run live in this repository.

## What exists today

| Format | In | Out | By | State |
|---|---|---|---|---|
| USD, USDA, USDC | yes | yes (`sim__stage__save`, flattened) | `assets__import_usd`, `assets__import_path`, packs | tested |
| USDZ | yes (bounded subset) | no | `assets__import_path` | tested |
| URDF (plain) | yes | no | `assets__import_urdf`, packs, `sim__quick_start` | tested |
| **MJCF (MuJoCo XML)** | **yes** | no | **`assets__import_mjcf`** (this PR) | **tested** on the Menagerie UR5e, Franka Panda and Go2 |
| Public NVIDIA robots by name | yes | no | `sim__quick_start robot=` (franka, unitree_h1, unitree_g1, ...) | tested |
| Occupancy map (YAML + image) | no | yes | `sim__map__occupancy` | tested |
| Video, pictures | no | yes | screen and camera tools | tested |

## What Isaac Sim 6.2 ships that we do not use yet

Read from the install's extension list (`isaacsim/exts` and `extscache`):

| Extension | Does | Gap it would close |
|---|---|---|
| `omni.kit.asset_converter` | Converts between USD and assets like OBJ, glTF and FBX (its own description) | `assets__import_mesh`: a prop or object that is not a robot |
| `isaacsim.asset.exporter.urdf` | Exports a USD articulation back to URDF | `assets__export_urdf`: a robot out to ROS tools |
| `omni.kit.usdz_export` | Packages a scene into a USDZ archive | `sim__stage__export` as USDZ |
| `omni.kit.converter.cad` (Hoops, JT, DGN) | STEP, IGES, JT and other CAD to USD | A robot or fixture that only exists as CAD |
| `isaacsim.asset.importer.heightmap` | A heightmap image to terrain | Outdoor and legged-robot terrain |
| `omni.kit.converter.gsplat` | 3D Gaussian splats (`.ply`, `.spz`) to USD | A scanned scene as a backdrop |
| `isaacsim.ros2.urdf` | Imports the robot description from a running ROS node | A robot already running in ROS, without its files |
| `omni.importer.onshape` | Onshape cloud documents | Needs an account: skip |
| `isaacsim.asset.transformer`, `omni.asset_validator` | Restructure and validate USD | A check before a converted asset is trusted |

Not in the install: an **SDF (Gazebo) importer**. Gazebo robots and worlds are the biggest format with no native route.

## The MJCF importer, as shipped

`isaacsim.asset.importer.mjcf` wraps NVIDIA's open-source `mujoco-usd-converter`. What its changelog says it converts: bodies, hinge, slide, ball and free joints (with limits,
damping, armature), box, sphere, capsule, cylinder, plane and mesh geometry (STL and OBJ files), PNG textures, `UsdPreviewSurface` materials, actuators (as
`MjcActuator` prims) and gravity. What it documents as **not** converted: cameras, lights, sensors, keyframes, deformable `flex` and `skin`, inline mesh
topology (a mesh with no file), signed distance fields, `contype`/`conaffinity` collision filtering, contact pairs, texture patterns (so the checkerboard
ground of many Menagerie scenes is lost), and recursive frames. Defaults, classes and `attach` are baked down. Our tool imports through it and reports
what the converted copy holds; it does not claim more.

## How every importer must behave

These hold for URDF and MJCF today and are the rule for the next ones:

1. The host reads and checks the file first, with no code run: no xacro, no plugin, no download, no URL. DTDs and entities are refused.
2. Every file it names must be inside the folder the user approved. Absolute paths, URIs, backslashes, symlinks and `..` out of the folder are refused.
3. The approved folder is copied to a private snapshot and verified; the native importer reads only the snapshot.
4. The generated copy is checked (a physics variant, rigid bodies, joints) before it is imported, and the result says what it holds.
5. The approval command printed on a refusal approves the smallest folder that holds the files, and never a system or private folder.
6. Provenance: a pack built from a download records `origin`.

## Build order

| # | Item | Why | Notes |
|---|---|---|---|
| 1 | MJCF import | Most public robots (Menagerie) are MuJoCo XML only; one tester had to write its own converter | done in this PR |
| 2 | Safe xacro expansion | The UR, TurtleBot3, Franka and many ROS descriptions are xacro only; we refuse it, and agent testers keep hitting that | A restricted expander (properties, macros, includes, arithmetic through a whitelist), never the `xacro` program: xacro can run arbitrary Python |
| 3 | Mesh and prop import (glTF, GLB, OBJ, STL, FBX) | Objects to grasp, furniture, scanned props | `omni.kit.asset_converter`; same folder rules; units and up-axis refusals like the URDF path |
| 4 | URDF export | Round trip to ROS and other tools | `isaacsim.asset.exporter.urdf`; says what cannot be exported |
| 5 | `sim__quick_start` and packs for MJCF | One call for a Menagerie robot; a pack that records it | `assets__pack_build` refuses `.xml` for now |
| 6 | SDF (Gazebo) | The largest remaining format | No native importer and no reliable SDF to URDF tool; needs its own converter, so wait for evidence from agent tests |
| 7 | CAD (STEP), heightmaps, Gaussian splats, USDZ export | Niche, each a thin wrapper over a shipped extension | Add when a user asks |

Each item becomes a checklist entry in [CHECKLIST.md](CHECKLIST.md) before it is built, and a plain-request agent test decides whether the tool is easy to find
and use. Driving is covered separately: ROS 2 `/cmd_vel`, TF and Nav2 work today; a joint-trajectory tool and a gripper tool are on the checklist.
