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
- Asset packs at an uncommitted tree: the chess set passed with the tools alone, apart from needing a Y-up stage. A URDF robot did not pass: see the open list in the [checklist](../plans/CHECKLIST.md).

## Root causes found

**`sim__map__occupancy` crashing Kit: NVIDIA's generator overruns the heap, on all five 6.x builds.** `getDimensions()` truncates `(max - min) / cell` in float32, giving 118 where 119 rows exist, then `getBuffer()` writes occupied cells with no bounds check. A cell on the last row overruns the block, and Kit aborts with `malloc(): invalid size`, then hangs. Whether it happens depends on bounds and start, which is why earlier runs looked intermittent. A debugger caught the store at `index=13924, vector_size=13924` on 6.0.0.0 and 6.2, and the two functions disassemble identically in omap 2.2.1, 2.3.1, 2.3.2 and 2.3.5. Headless and windowed made no difference. The tool no longer calls `getBuffer()`: it builds the grid in Python from `get_occupied_positions()`. Before omap 2.3.3, a floor within one cell below a positive `z_min` also fills the slice, which explains the old all-free maps; the tool raises `z_min` there and reports `z_used`. Maps whose size was truncated gain a row and column: 118 becomes 119.

Unpatched, a wall crossing the top edge aborted on 6.0.0.0, 6.1 and 6.2, and a full floor with default `z` aborted on 6.0.0.0, 6.0.0.1 and 6.0.1. With the buffer built in Python, 19 runs on the three old builds gave 0 aborts. At `e8a044c`, 26 runs of the crash scenes across 6.0.0.0, 6.0.0.1 (including windowed), 6.0.1 and 6.1 gave no abort and no `malloc`, `corrupted` or `free():` in any Kit log, in 0.21 to 0.39 s each. Every build, including 6.2 and 5.1, produced byte-identical PGMs for each scene, and viewed maps show the wall and block in place. Cells merely touching a box's +x and +y faces are marked, so a 0.4 m box covers 9 by 9 cells.

**Non-finite physics after "reopening a workstation": a collider overlapping the robot, not save or reopen.** The tester's saved file alone played with 0 errors on fresh windowed 6.0.0.1. The same file with the 2 m wall that tester had added gave 102 non-finite errors and 750 invalid-transform warnings. The wall gave the same errors with no save or reopen on every build (1,897 to 2,411 over 8 s on 6.0.1, 6.1 and 6.2), and save and reopen with no wall gave 0 on all of them. Why PhysX goes non-finite under this overlap was not investigated.

## Not established

- The single windowed crash on the tester's workstation scene was not reproduced: the message matches, but there is no stack from that Kit.
- Why Kit hangs after the abort instead of exiting.
- Windowed sweeps ran at `e658289`, before these fixes. Nothing here covers long runs, Newton, or real robots.
