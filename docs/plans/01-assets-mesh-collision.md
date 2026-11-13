# Plan 01: mesh preparation, validation, and the work-cell pack rebuild

Roadmap [Phase 1](../ROADMAP.md#phase-1-assets-that-load-on-newton). Two parts: **A** is code and runs on a CPU; **B** rebuilds
the private pack and needs the owner.

## Problem

The UR5e work cell refuses to build on Newton. The first blocker is the Vention bench: its 385 triangle-mesh colliders (1.63 M
triangles) ask Newton for 7,500,052 triangle pairs against a buffer of 1,000,000. A probe showed that convex hulls per bench
mesh avoid it ([open item](../OPEN-ITEMS.md#work-cell-on-newton)). The owner wants the detailed mesh kept for rendering and a
separate, simple collision layer added. Three other blockers in the same probe are detectable from the files alone: the RG2's
passive joints carry a drive with `maxForce` 0, the RG2 is a second articulation under the arm's, and moving bodies end up
below MuJoCo's minimum mass.

Decimating the render mesh does not fix the overflow and is not required here; `decimate` exists for render cost and for
shrinking a hull's source.

## Design

Two host tools in the `assets` toolset. Neither needs Kit. They read and write files only inside a pack version or an
approved root, exactly as `assets__pack` does, and they never change a file in place: output goes to a new folder or a new
pack version.

### `assets__mesh` (grouped)

| Action | Does | Key arguments |
|---|---|---|
| `inspect` | Per mesh prim: triangles, bounds, watertight or not, whether it is a collider and with which approximation. Totals for the file. Read-only | `source_path` or `pack` + `asset_id` |
| `decimate` | Quadric decimation of render meshes | `target_tris` or `ratio`, `prims` |
| `hulls` | A collision shape per mesh: `method="box"` (oriented box), `"convex"` (one hull) or `"coacd"` (convex decomposition) | `method`, `max_hulls`, `threshold`, `prims` |
| `split` | Writes the asset as two layers, render and collision, plus the file that composes them | `output`, `collision` (the options of `hulls`) |

`split` output, for a source `bench.usdc`:

```text
bench/
  bench.usda             the asset: sublayers the two below; this is what an asset_id points at
  bench_render.usdc      the original meshes, every collision schema removed
  bench_collision.usdc   collision shapes only, purpose "guide", CollisionAPI applied
```

- Collision shapes are explicit geometry: `UsdGeom.Cube` for boxes, small convex `UsdGeom.Mesh` prims with
  `physics:approximation = "convexHull"` for hulls. Both engines read the same shapes; nothing depends on an engine's own
  mesh cooking. Shapes sit under the same body prim as the mesh they stand for, so rigid bodies and joints are untouched.
- `purpose = "guide"` keeps them out of renders.
- Long actions (`hulls` with `coacd`, `split`) return a `job_id` in the shared jobs folder; `inspect` answers directly.
- The work runs in a subprocess of the host's own interpreter (`python -m twinsmith.mesh_worker`), so the server stays
  light. It needs the optional `mesh` extra; without it the tool answers `MESH_EXTRA_MISSING` with the install command.

### `assets__validate` (one call)

`assets__validate(source_path | pack, engine="newton" | "physx", kit=false)` returns `{ok, engine, checks: [{id, level, ok,
prim, detail, fix}], totals}`. It opens the USD with `usd-core`, read-only. Rules are data in one table, each with the
evidence it came from:

| Id | Level on Newton | Rule | Evidence |
|---|---|---|---|
| `UNITS` | error | `metersPerUnit` and `upAxis` are authored | Import contract |
| `TRI_COLLIDER` | error | No collider is a triangle mesh without a convex approximation when the file's collision triangles exceed the budget (default 200,000; one constant) | 1.63 M triangles needed 7.5 M pairs |
| `COLLIDER_RENDERED` | warning | A collision-only shape has `purpose` other than `guide` | Split contract |
| `DRIVE_ZERO_FORCE` | error | A joint drive has `maxForce` 0 | MuJoCo: "actfrcrange[0] should be smaller than actfrcrange[1]" |
| `NESTED_ARTICULATION` | error | An articulation root lies inside another articulation's joint tree | Importer mis-lists bodies; weld target unresolved |
| `MASS_MIN` | error | A moving body has no mass or inertia, or one below MuJoCo's minimum | "mass and inertia of moving bodies must be larger than mjMINVAL" |
| `DRIVE_GAIN` | warning | A drive's stiffness is above what 500 Hz can hold (state the formula used) | 9400 N m per degree went non-finite |
| `LOOP_JOINT` | warning | A joint excluded from its articulation has no equality or mimic constraint | The RG2 fingers do not move |

On `engine="physx"` the Newton-only rules are reported as `info`. `kit=true` additionally runs Isaac Sim's Asset Validator
through the bridge and appends its findings; first confirm in a running Kit that the extension is present in 6.2, and if it
is not, leave `kit` out of this pull request and say so. The budget in `TRI_COLLIDER` is a starting value: record the pair
count Newton reports for pack `v2` in Part B and adjust the constant with that number in the comment.

Ideas to read, not code to copy: Isaac Sim's own `physx-to-newton-conversion` and `isaac-sim-validator` agent skills.

## Part A: files

| File | Change |
|---|---|
| `mcp/pyproject.toml` | New extra `mesh`: `trimesh>=4,<5`, `coacd==1.0.11`, `fast-simplification`, `usd-core>=25.11,<27`, `numpy`. Then `uv lock --directory mcp` |
| `mcp/twinsmith/mesh_ops.py` (new) | Pure array functions: `inspect`, `decimate`, `box`, `convex`, `coacd`. No USD, no I/O |
| `mcp/twinsmith/mesh_usd.py` (new) | Read mesh prims, author the three `split` files, strip collision schemas |
| `mcp/twinsmith/mesh_worker.py` (new) | `python -m` entry: one action, JSON in, JSON result and job record out |
| `mcp/twinsmith/asset_rules.py` (new) | The rule table and `validate()` |
| `mcp/twinsmith/asset_packs.py` | Dispatch the new canonical names where `assets.pack_build` is dispatched; reuse `_approved_root`, `_pack_argument` |
| `mcp/twinsmith/tool_groups.py`, `toolsets.py` | Group `assets__mesh`; add both roots to the `assets` toolset |
| `mcp/twinsmith/tool_hints.py`, `catalogs/assets.json` | Hints; regenerate the catalog |
| `mcp/tests/test_mesh_ops.py`, `test_mesh_usd.py`, `test_asset_rules.py` (new) | Below |
| `docs/ASSET-PACKS.md`, `docs/ASSET-IMPORT.md`, `docs/TOOLS-ARGUMENTS.md` (generated), `docs/OPEN-ITEMS.md` | Document; tick the bench item's follow-up |
| `THIRD-PARTY-NOTICES.md` | Only if code is copied. Dependencies in an extra need no entry |

Keep each new module under about 250 lines. If `mesh_usd.py` and `sim_tools/mesh_import.py` end up duplicating collider
authoring, share one function rather than keep two.

## Part A: tests (CPU, in CI)

- A generated fixture: an L-shaped mesh of 20,000 triangles as a collider, plus a small articulation with a zero-force drive
  and a nested articulation root. No private asset is used.
- `decimate` reaches the target within 5 percent and keeps the bounds within 1 percent.
- `hulls`: `box` gives one cube per mesh enclosing it; `coacd` gives at most `max_hulls` convex pieces, each convex.
- `split`: the composed file has the same body and joint prims as the source; the render layer has no `CollisionAPI`; every
  collision prim has `purpose = "guide"`; the source file's hash is unchanged.
- `validate` fails the fixture with `TRI_COLLIDER` (budget lowered in the test), `DRIVE_ZERO_FORCE` and
  `NESTED_ARTICULATION`, and passes the fixture after `split` and after the two physics faults are removed.
- Without the `mesh` extra importable, `assets__mesh` answers `MESH_EXTRA_MISSING` and the server still starts.
- A path outside a pack or an approved root is refused, as for `assets__pack`.
- `test_toolsets.py` listing budgets still pass; raise a budget only with the measured number in the comment.

## Part B: rebuild the pack (the owner, or an agent with the owner's go-ahead)

Needs the private `ur5e-jenga-workcell/v1` pack and, for the last step, a free GPU.

1. `assets__mesh(action="inspect", pack="ur5e-jenga-workcell/v1", asset_id="bench")`: confirm 385 colliders, 1.63 M triangles.
2. `assets__mesh(action="split", ..., collision={"method": "box"})` into a staging folder. The bench is extrusions and
   plates, so boxes should fit; use `coacd` only for meshes where the box volume is more than twice the hull's.
3. Fix the three robot findings in the staged copy: remove the zero-force drives, merge the RG2 into the arm's articulation,
   author the explicit gains from the probe.
4. `assets__pack(action="build")` the result as `ur5e-jenga-workcell/v2`. `v1` is not touched.
5. `assets__validate(pack="ur5e-jenga-workcell/v2", engine="newton")` passes.
6. Live, on a PhysX session first: `scripts/workcell_smoke.py` with the configuration pointed at `v2` still passes.
7. Live, on a Newton session, with the refusal bypassed as in the earlier probe: the bench loads with no triangle-pair
   overflow. Keep the JSON report outside the repository and record the result in `docs/OPEN-ITEMS.md`.

The refusal in `exts/workcell/workcell/scene.py` stays until [Phase 2](../ROADMAP.md#phase-2-the-work-cell-on-newton).

## Done when

- Part A: all tests above pass; the CI commands in the [plans index](README.md) pass; `default` and `all` listing sizes are
  re-measured and written in `docs/TOOLSETS.md`.
- Part B: steps 5 to 7 are recorded with the engine, the Isaac Sim build and the loaded Newton version.
