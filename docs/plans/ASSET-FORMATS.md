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

## The design rule: formats are options, not tools

Kaleb's rule (2026-11-05): no new tools. An agent finds one import entry, `assets__import_path`, and the format is an option on it: `auto` by default (from the
extension and the first bytes), settable to override. `assets__import_usd`, `assets__import_urdf` and `assets__import_mjcf` stay as thin aliases until the
tool-surface review (CHECKLIST section 3) decides, with Kaleb's approval, whether to fold them away. The same holds for the other tools: export is a `format` on
`sim__stage__save`, a downloaded robot is a `robot_file` on `sim__quick_start`, and `assets__dependencies`, `assets__scan`, `assets__search` and the pack tools
read every supported format. Every result has one shape: articulation root, movable joints with limits, bounds, and what the converter dropped.

## What testers found (wishlist-3, tester C, main before PR 82)

| Source | Format | Result | Gap |
|---|---|---|---|
| UniversalRobots ROS2 description (UR10e) | xacro (with `.dae` meshes) | Only after six calls expanding it with the system `xacro`; one near-miss resolved the system's own `ur_description` | F29 safe xacro expansion; the expanded URDF with 14 `.dae` meshes then imported fine |
| usd-wg/assets (Teapot) | USD | Worked | none |
| Khronos glTF-Sample-Assets (Duck) | glTF | No route at all (the import is USD only) | F30 mesh import |
| gz-sim `diff_drive.sdf` (blue vehicle) | SDF | Rebuilt by hand from primitives, no joints | F31 SDF reader |
| Menagerie (Panda, UR5e, Go2, H1), tester D | MJCF | All four loaded, moved and shown in one scene (verified) | F44: torque-driven robots have no drives |

## Build order

| # | Item | Checklist | Notes |
|---|---|---|---|
| 1 | MJCF import | done in PR 82 | |
| 2 | One import entry with `format=auto`, one result shape | F28 | Foundation for everything below; aliases stay |
| 3 | Fewer calls: sticky bridge or `port`, a result that is enough to act on, `pose` / `joint_targets` / `ground` on the import | F41 | Measured with the same plain-request prompts before and after |
| 4 | Safe xacro expansion with `xacro_args` | F29 | A restricted expander, never the `xacro` program: xacro can run arbitrary Python |
| 5 | Mesh and prop import (glTF, GLB, OBJ, STL, FBX, DAE, PLY) and `.dae` in URDF | F30, F32 | `omni.kit.asset_converter`; units and up axis stated |
| 6 | SDF (Gazebo) reader | F31 | No native importer exists; restricted host-side reader into the same checked path |
| 7 | `assets__dependencies` for every format; model folders and bundles | F35, F36 | |
| 8 | MJCF and others in packs and `sim__quick_start` | F34 | `robot_file` beside `robot=` |
| 9 | Export (`sim__stage__save format=urdf/usdz`) | F33 | `isaacsim.asset.exporter.urdf`, `omni.kit.usdz_export` |
| 10 | Maps, heightmaps, CAD, Gaussian splats | F37, F38 | Thin wrappers over shipped extensions; the `format` list names them |
| 11 | Validation of every converted asset | F39 | Reported in the import result |

Each item is ticked only after a plain-request tester (F40) loads one real asset of that format from a named repository.

## Test matrix

One real asset per row, downloaded by the tester from the named place, loaded through the single entry (`assets__import_path`, `format=auto` unless the row says otherwise), moved if it can move, photographed. "Named place" is the repository to try first; if it has moved, the tester says so and takes the closest official one. Nothing is ticked without the run's evidence in `~/isaac-workspace/runs/`.

| # | Row | Named place | What must hold |
|---|---|---|---|
| 1 | USD | usd-wg/assets (Teapot) | loads; textures resolve (tester C saw a lime-green teapot: check it is the asset and not a lost texture) |
| 2 | MJCF | MuJoCo Menagerie (Panda, UR5e, Go2, H1) | **verified** (Gemini 3.8 Flash, windowed, 2026-11-05): all four load and move; torque-driven Go2 and H1 need gains (F44) |
| 3 | URDF with `.dae` meshes | Universal_Robots_ROS2_Description, TurtleBot3 | meshes visible, not empty links; units correct |
| 4 | xacro with arguments | Universal_Robots_ROS2_Description (`ur_type`) | expanded by the host reader; same result as the system `xacro`; hostile xacro (`python:`, `eval`, a path out of the folder) refused |
| 5 | glTF / GLB, OBJ, STL, FBX | Khronos glTF-Sample-Assets (Duck), a Menagerie or ROS mesh | right scale and up axis stated; collider option works; a prop falls and rests on the ground |
| 6 | SDF model | gz-sim `diff_drive.sdf` | links, joints and wheels move; drives from a joint command; `model://` includes resolve inside the folder only |
| 7 | Bundle: Gazebo model folder | a Gazebo Fuel model (with `model.config`) | the entry file is chosen without asking; several candidates are listed |
| 8 | Bundle: ROS package | a robot's ROS description package | `package://` resolved; one approval for the package folder |
| 9 | Bundle: Menagerie folder | `unitree_go2/` (has `scene.xml` and `go2.xml`) | the right entry for a robot and for a scene |
| 10 | Bundle: LeRobot / Hugging Face robot repo | an SO-100/SO-101 style arm repo | URDF or MJCF found under its own layout; loads and moves a joint |
| 11 | Nav2 map | `turtlebot3_world` map from the Nav2 bringup package | floor plan matches the picture; a robot drives in it |
| 12 | Export | any imported robot | `sim__stage__save format=urdf` loads again in a fresh scene; what was lost is listed |
| 13 | Hostile inputs per format | made by us | every one refused with a clear code and no file read outside the folder |
| 14 | Fewer calls | the tester prompts above | calls per task counted before and after; target 3 or fewer for "load, pose, photograph" |
