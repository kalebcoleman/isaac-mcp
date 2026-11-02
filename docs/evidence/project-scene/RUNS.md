# Measured release/backend results

This table retains the earlier qualification and its failures. The newer main-base validation,
camera fixes and expanded all-tool gate are tracked in [final validation](FINAL-VALIDATION.md)
and the [99-tool ledger](ALL-TOOLS-MATRIX.md). Historical passes below do not establish the
current combined candidate or completion of the expanded gate. In particular, the later
independent URDF mass/body readback found missing physics in native variant output;
historical import success is not a URDF physics pass. See the retained failure and fix
qualification in FINAL-VALIDATION.md.

All rows are real MCP client/server calls into disposable, visible Isaac Sim processes on
Ubuntu 24.04.5, kernel 7.0.0-38-generic, RTX 5080 16,303 MiB, driver 580.178.04,
Python 3.12.3, host MCP 1.30.0. [Exact installed builds](INSTALLATIONS.json) distinguish
6.0.0.0 from 6.0.0.1; core `get_version()[0]` alone does not. [Compact call inventories](RUN-SUMMARIES.json)
record each tool and product SHA. Full transcripts/logs are release-bundle members under `runs/`
and `sessions/`. Failed attempts are preserved alongside retests.

| Release | Full applicable PhysX suite | Sensors | Actual independent agent | Follow-up |
|---|---|---|---|---|
| 6.0.0.0 | `final 6000`: core32, expanded48, edges23, clear4; all 37 tools | 217 captures at64²/320×240/1024×768; legacy full-scan LiDAR with rotated sensor and measured2.5/4.5m planes |65 calls; measured fall/rest and joint movement; strict1mrad joint tolerance failed; first camera image stale | `review6000-cold2`: five cold-camera cycles pass after CPU priming + four render completions. Earlier saved native graph reopen failed; export fix removes the invalid-graph creation error, but camera reads then remained NOT_READY despite longer warm-up and a render-tree-free diagnostic copy. Later `/Orchestrator` export correction and fresh-process passes are recorded in FINAL-VALIDATION.md; this historical failure is retained. |
| 6.0.0.1 | `final 6001`: same full suite, all 37 |217 captures, measurable LiDAR; no native camera crash with copied CPU storage |61 calls, no MCP errors; physical rest/image agreement; fixture residual2.59mrad fails stricter1mrad criterion | Old borrowed-GPU camera crashes remain in historical bundle. Build-specific owned-CPU stack qualified, not a blanket6.0 allowlist. |
| 6.0.1.0 | `final 601`: same full suite, all 37; `review601` repeats core/expanded/stress after cleanup fix |217 captures in each run; measured geometry and timing |61 calls;28/29 checks pass, fixture residual1.77mrad | Old run crashed natively during UR5e after sensor-stage replacement. Cleanup fix leaves graph teardown to Kit; fresh retest completed full sensor suite and two UR5e rebuild sequences (first19/20 only because harness expected6.2; corrected20/20). |
| 6.1.0.0 | `review61`: same full suite, all 37 |217 captures and measured LiDAR |65 calls; sample fell1.609→1.323→0.700m, stable2s;240 steps=4s at1/60; fixture residual2.85mrad | Initial and resting images and real app window inspected; no runtime/ USD discrepancy. Product `faaf73d`. |
| 6.2.0.0 | `final 62` full suite; `review62` repeats after save/readback/cleanup changes |217 captures per completed stress run; measurable LiDAR |118 calls; measured rest and stable2s; initial retained-snapshot quota failure led to verified reuse; real-robot joint targets not all within0.01rad | Final owner-requested verify/smoke/motion and existing `/physicsScene` checks are recorded in the final validation release attachment. |

Counts describe exercised behavior, not exhaustive edge-case or lifetime guarantees. Every capture
is a latest rendered frame, not an atomic physics/sensor snapshot. Independent joint residuals are
reported rather than turning target acknowledgement into claimed target attainment. Body pose,
velocity/mass, timed steps and observed joints—not screenshots—support the physics claims.

Newton: separate qualified MuJoCo-backend trials on **all five** releases measured two cycles of
fall/collision/rest, positive/negative joint commands,10/60/120 exact steps, timing at1/120,
Play/Pause/Stop and exported poses. Release-specific SimulationManager/Warp/Newton/MuJoCo
identities and adapters appear in `runs/newtontrial*-probe.json` and corresponding native logs.
These are representative engine tests, **not the complete37-tool suite or independent agent
workflow for every Newton release**. Meter-stage and recorded-stack checks remain mandatory;
unknown/conflicting versions fail closed. No PhysX observation substitutes for Newton state.

No new live Isaac Lab training or physical hardware tests were run here. Host Lab/session/gateway
regressions are in CPU CI. Prior Lab and PR9 results remain distinct. Unknown/future releases,
other GPU/driver combinations, all non-MuJoCo Newton solvers, Wayland and other operating systems
are unverified. The first-slice merge does not close these gaps.
