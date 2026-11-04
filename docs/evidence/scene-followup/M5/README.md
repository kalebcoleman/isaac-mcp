# M5: editing any prim in the scene

Tools: `sim__prim__set_transform` on any Xformable prim, new `sim__prim__set_attribute`,
`sim__physics__apply` (collider, rigid body, kinematic, mass, density) and `sim__prim__revert`, plus
`Plane`, `mass` and `density` on `sim__prim__create`. Every edit is a session-layer opinion with
before/after values; source files are hashed before and after.

## Environment

| | 6.2 | 5.1 |
|---|---|---|
| Isaac Sim | 6.2.0.0 | 5.1.0 |
| Backend / device | PhysX / CUDA | PhysX / CUDA |
| Kit | headless, port 18901, stopped by PID after the run | same |
| Scene | a USD written by the script: a static-collider table, a visual-only object, a two-op Xform, an animated Xform and an instance from a second written file | same |
| Robot | `sim__quick_start` public `franka` | same |
| ROS | domain 111 | same |

Command: `ISAAC_MCP_BUILTINS=1 ISAAC_MCP_LOCAL_TOOLS=all uv run --isolated --python 3.12
--with-editable . --extra scene-test python ../scripts/validate_scene_m5_edit.py --port 18901
--output <tmp>/m5-<build>.json --directory <tmp>/out --robot franka`.

The written scene is in `<tmp>/out/m5-scene.usda` (+ `block.usda`); the run creates it, so the source
files exist only under the scene output root, never in the repository.

## Results

| Check | 6.2 | 5.1 |
|---|---|---|
| Move a table the pack did not create, with before/after pose and `source_files_changed=false` | pass | pass |
| Move a prim with a two-op transform stack; the override is one op and `revert` restores the stack | pass | pass |
| Animated transform refused `ANIMATED_TRANSFORM` | pass | pass |
| `xformOpOrder` through `sim__prim__set_attribute` refused `ENGINE_OWNED_ATTRIBUTE` | pass | pass |
| Instance proxy refused `INSTANCE_PROXY`, naming the instance root | pass | pass |
| Removing a source-authored collider refused `READ_ONLY_SOURCE` | pass | pass |
| `sim__prim__create` adds a `Plane` with `collision=true` (type and collision readback) | pass | pass |
| `sim__prim__set_attribute` writes `visibility=invisible` and creates a typed `user:count=3` (int) | pass | pass |
| Collider + `convexHull` + rigid body applied to the visual-only object, with the kinematic flag off | pass | pass |
| A body edit while playing refused `TIMELINE_ACTIVE` | pass | pass |
| The object falls from z=1.2 and rests on the table at z=0.625 (table top 0.525 + half height 0.1) | pass, z=0.625 | pass, z=0.625 |
| Two cubes created with `mass=2.5` and `mass=5.0` report those runtime masses via `sim__body__state include_dynamics` (`mass` and `mass_kg`) | pass, 2.5 / 5.0 kg | pass, 2.5 / 5.0 kg |
| A rigid body under a rigid-body ancestor refused `NESTED_BODY` | pass | pass |
| `sim__prim__revert` removes the recorded session opinions on the object, table and two-op Xform and restores the authored matrices | pass | pass |
| Source USD SHA-256 unchanged after the run | pass | pass |
| Public franka: moving a joint-attached link refused `ARTICULATION_LINK`, naming `/World/Franka`; moving the robot and reverting it works | pass | pass |

Refusal messages name the next step: the animated case suggests a parent Xform, the instance case the
instance root, the source-collider case the source file, and the link case the robot Xform in a
`retry_with` call.

## Not verified

6.0.0.0, 6.0.0.1, 6.0.1.0 and 6.1.0.0 (not installed here) and Newton. No video or screenshot: the
run is headless and the proof is the numeric rest height, the runtime masses and the file hashes.
Instance-proxy and nested-body refusals are proven on a written scene and a copied robot, not on every
asset kind. Removing an API schema that a source file authored is refused rather than emulated, so a
source collider must be edited in the source file; that limitation is intentional.
