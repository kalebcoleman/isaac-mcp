# workcell extension

**RISE lab's UR5e digital twin: Kaleb's work with that lab and that robot.** The rest of
this project is robot-agnostic; use `sim__quick_start` for a generic scene.

The extension integrates the pack-backed Workcell API with the shared
`twinsmith.bridge`; it has no socket server or UI panel of its own. The
workcell integration provides seven grouped tools and ten actions:

| Group | Actions |
| --- | --- |
| `workcell__cell` | `quick_start`, `status`, `shutdown` |
| `workcell__robot` | `status` |
| `workcell__perception` | `status` |
| `workcell__jenga` | `build`, `status` |
| `workcell__real2sim` | `status` |
| `workcell__planning` | `status` |
| `workcell__eval` | `status` |

`quick_start` resolves the external `ur5e-jenga-workcell/v2` pack and builds a stopped
PhysX scene. `physics` mode uses gravity; `mirror_real` uses zero gravity but does not
synchronize with hardware. An unsaved stage is replaced only with explicit consent.
`shutdown` releases workcell state without clearing the stage. Robot, perception and
Jenga status actions read build reports; perception status is not sensor capture.
Real-to-simulation synchronization, planning and evaluation remain unimplemented.

There is no legacy extension ROS graph setup or custom `rclpy` build prerequisite for
this API. Generic `sim__*` tools are separate from the workcell interface.

**Candidate, not runtime-qualified:** integration does not establish a PhysX smoke,
render, sensor or physical-fidelity pass. The seeded Jenga build action replaces the
tower on a stopped timeline. Its default `measured_pending` profile and seed 42 are
unconfirmed, overwriteable configuration, not measurements. Nominal dimensions remain
72 × 24 × 14 mm. See the [variation and reset contract](../../../docs/TOOLS-WORKCELL.md#seeded-jenga-variation)
for distribution fields, sampling bounds, and the distinction between rebuilding and
restoring realized initial poses without resetting velocities or solver state.

- Workcell configuration, fidelity gaps and pack creation: [workcell README](../workcell/README.md)
- Hardware inventory and recorded prices: [BOM](../workcell/BOM.md)
- Tools and behavior: [workcell tools](../../../docs/TOOLS-WORKCELL.md)
- Arguments: [tool arguments](../../../docs/TOOLS-ARGUMENTS.md#ur5e-twin-workcell__)
- Generic scenes: [scene tools](../../../docs/TOOLS-SCENE.md)
- Asset policy: [asset packs](../../../docs/ASSET-PACKS.md)

The repository ships no pack binaries. Set `TWINSMITH_ASSET_ROOT` to override the asset
root; the loader appends `ur5e-jenga-workcell/v2` (the version in `config/lab_hardware.json`). Configuration uses logical asset IDs,
not file paths or a legacy-store fallback. Preserve the manifest, provenance, hashes and
dependency checks described in the workcell README when creating a private pack.
