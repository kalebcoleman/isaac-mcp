# UR5e workcell tools

The workcell uses externally supplied, pack-resolved assets. It does not ship robot binaries.
The workcell tool builds a stopped PhysX scene; the default mode uses gravity and
`mirror_real` uses zero gravity. Replacing an unsaved stage requires explicit consent.
The stage remains in place after shutdown. Generic `sim__*` tools remain separate.

- `ur5e_dt__workcell_quick_start` builds the workcell.
- `ur5e_dt__workcell_status` reads its report.
- `ur5e_dt__workcell_shutdown` clears workcell state without clearing the stage.
- `ur5e_dt__robot_status` reads the robot report.
- `ur5e_dt__perception_status` reads camera configuration, not sensor capture.
- `ur5e_dt__jenga_status` reads the tower report.
- `ur5e_dt__real2sim_status` reports that real-to-simulation is not implemented.
- `ur5e_dt__planning_status` reports that planning is not implemented.
- `ur5e_dt__eval_status` reports that evaluation is not implemented.

There is no force capture, motion planning, real-hardware control, or evaluation implementation.
See [tool arguments](TOOLS-ARGUMENTS.md) for schemas and [asset import](ASSET-IMPORT.md) for generic assets.
