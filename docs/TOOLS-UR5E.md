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
- `ur5e_dt__jenga_build` replaces the current tower on a stopped timeline.
- `ur5e_dt__real2sim_status` reports that real-to-simulation is not implemented.
- `ur5e_dt__planning_status` reports that planning is not implemented.
- `ur5e_dt__eval_status` reports that evaluation is not implemented.

There is no force capture, motion planning, real-hardware control, or evaluation implementation.
See [tool arguments](TOOLS-ARGUMENTS.md) for schemas and [asset import](ASSET-IMPORT.md) for generic assets.

## Seeded Jenga variation

Seven grouped tools expose ten actions. Use the twin's Jenga group's `build` action
with `profile="measured_pending", seed=42` (single-purpose alias
`ur5e_dt__jenga_build`) to rebuild
the tower in the current workcell. This is destructive: it replaces the old tower,
not the rest of the workcell. Stop the timeline first. Omitted `profile` and `seed`
use `jenga.variation_profile` and `jenga.variation_seed` from the hardware configuration.
A selected profile requires an integer seed. This wiring is CPU-tested, not a PhysX,
render, sensor or physical-fidelity qualification.

The nominal block dimensions remain **72 × 24 × 14 mm**. The supplied
`measured_pending` profile and seed `42` are explicitly **unconfirmed** defaults,
not measurements: uniform mass 0.014–0.016 kg, static friction 0.28–0.34,
dynamic friction 0.22–0.27, and additive size tolerance −0.0002–0.0002 m
independently on each axis. Replace these provisional numbers with measured values.
Edit the `value`, `status`, and `source` wrappers for `variation_profiles`,
`variation_profile`, and `variation_seed`; distribution objects live inside
`variation_profiles.value`. Set `variation_profile.value` to JSON `null` to disable
variation and use nominal dimensions and scalar mass/friction. Omitting the tool
argument does not disable a configured profile.

Each named profile has exactly `mass_kg`, `static_friction`, `dynamic_friction`, and
`size_tolerance_m` (three distribution objects). Supported distributions are:

| `distribution` | Additional fields |
| --- | --- |
| `constant` | `value` |
| `uniform` | `min`, `max` |
| `normal` | `mean`, `std`, `min`, `max` |

Values must be finite; mass is positive and friction nonnegative. Normal distributions
require positive standard deviation and a mean inside their bounds. Size tolerance
is limited to ±0.001 m by a software guard, not a measured manufacturing specification.
Bounded normal draws and friction pairs with dynamic friction above static friction
are rejected and retried up to 128 times, never clipped. Only the selected profile
is validated. Sampling uses a local seeded generator, leaving global random state alone.

The report records the profile, seed, sampling policy and each block's mass, friction,
dimensions, tolerance, initial position and rotation. Each block receives its own
physics material. Sampled widths center rows; alternating layers rotate 90 degrees
and sit above the tallest block below. Shorter blocks can leave gaps that need settling;
tower position and orientation are not independently randomized.

Rebuilding samples again (the same configuration and seed reproduce the realization).
The internal Jenga pose-reset helper instead restores recorded initial transforms;
it does not resample, restore velocities or reset solver state. It requires the caller
to stop the timeline and is not a separate exposed workcell action.
