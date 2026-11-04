# PR 70 per-build retest, 2026-11-04

Testers on one RTX PRO 6000 Blackwell workstation (Ubuntu 22.04), plus 6.2 and 5.1 on an RTX 5080 desktop (Ubuntu 24.04). PhysX. Headless unless stated. Older-build tables ran at the commit named; these are short checks, not long runs.

## Universal flow at `c90e0ab`

| Build | `sim__quick_start` with UR5e, joint moved and held | TF and `/joint_states`, no oscillation | `/scan` from a config LiDAR | Stale file rescue gives `/scan` | Map with a real collider | `smoke_tools.py` |
|---|---|---|---|---|---|---|
| 6.0.0.0 | pass | pass | pass | pass, before Play | pass 3/3, about 0.4 s | 13/16, empty asset store |
| 6.0.0.1 | pass | pass | pass | pass | pass 3/3 | 20/20 |
| 6.0.1.0 (standalone) | pass, no extra environment | pass | pass | pass | pass with a raised slice, see below | 13/16, empty asset store |
| 6.1.0.0 | pass | pass | pass | pass | pass | 20/20 |

## Fixes verified at `16859d8` and `7279cb5`

| Check | 6.0.0.0 | 6.0.0.1 | 6.0.1.0 | 6.1.0.0 |
|---|---|---|---|---|
| Clock and `/scan` graphs through `sim__graph__build`, no false refusal | pass | pass | pass | pass |
| ConstantToken to a text input refuses with `CONNECTION_REFUSED` | pass | pass | pass | pass |
| `sim__lidar__read`, 36 default calls while playing | 36/36 (12/36 before) | 36/36 | 36/36 | 36/36 |
| One `/joint_states` publisher after save, reopen and rebuild, over three more cycles | pass | pass | pass | pass |
| One `/tf` and one `/clock` publisher on the rebuilt stage | pass | pass | pass | pass |

Also on 6.0.1.0: `sim__map__occupancy` with a box floor and default `z` reports `z_used` and a correct PGM; `verify.sh` passes all checks; `verify_tf.sh` and `probe_62.sh` exit 0 and leave nothing listening.

On 6.2.0.0 and 5.1.0.0 (desktop, `bc3e24d`): the same clock and `/scan` graphs build. Default reads were 12/12 on 6.2 and 28/30 on 5.1, where the misses were all-zero first scans, later addressed by `e8dbd94` and not re-run. A real tiny Cartpole job ran through the MCP tools on Lab 3.0.0 + 6.2 and on Lab 2.3.2 + 5.1.

## Goal-only runs by a weaker model

- UR5e at `c90e0ab`, 6.2 windowed, empty asset store: all six goals in about 9.5 minutes with no `run_python`. Two calls gave a connected TF tree. A saved scene reopened in a new process.
- Lab 3 at `e658289`, 6.2 windowed, Parts A to J including Nav2, with no `run_python`. Two Nav2 goals failed on a table inside the LiDAR's 1 m blind zone, and a third in a clear corridor succeeded.
- Asset packs at an uncommitted tree: the chess set passed with the tools alone, apart from needing a Y-up stage. A URDF robot did not pass: see the open list in the [checklist](../plans/PR-70-CHECKLIST.md).

## Not established

- `sim__map__occupancy` wedged 3/3 on 6.0.0.0 and 1/1 on 6.0.0.1, and once crashed with `malloc(): invalid size`, at `e658289`. Later runs here did not reproduce it, with no fix aimed at it, so treat it as intermittent until explained.
- A reopened workstation played with non-finite PhysX bounds once, windowed on 6.0.0.1 at `e658289`. Headless reruns at `c90e0ab` gave 0 errors. Unexplained.
- Windowed sweeps ran at `e658289`, before these fixes. Nothing here covers long runs, Newton, or real robots.
