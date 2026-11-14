# Open items

The unfinished work from the old working checklist, grouped by topic so each group can become an issue. Check an item only
after verifying it against the current code and the relevant Sim and Lab build. Current behaviour and tool arguments are in the
[documentation index](README.md).

Keep the MCP focused on reusable capabilities. Small Jenga and warehouse quick starts remain; substantial tasks, policies,
missions, checkpoints and run data belong in separate projects. Preserve existing tool contracts and user assets when making
changes. Do not start or stop unrelated jobs; record the source, versions, backend, observed effects and limits of a test.

## Newton qualification

Newton is the default engine for the scenes these tools build and PhysX is opt-in; the statement and the known limits are in
[compatibility](COMPATIBILITY.md#target-engine). Each result records the engine, the Isaac Sim build and the loaded Newton
version.

- [x] Hold a full 54-block Jenga tower for ten seconds on Newton with the contact-buffer check on (500 Hz; 1.7 mm).
- [ ] Make a Franka joint move settle within the PhysX tolerance on Newton.
- [ ] Decide `sim__physics(action="raycast")` on Newton: implement a query, or keep it refused and say so.
- [ ] The UR5e work cell on Newton: see [Work cell on Newton](#work-cell-on-newton) below.
- [ ] Record the live runs that show exact stepping, the one-off push and camera capture after motion passing on Newton; they
  are reported, not yet recorded in this repository. Run `scripts/smoke.py sim --backend newton --discard-changes
  --check newton_checks` on a free GPU and keep the JSON report. If a step-timing mismatch remains, read `newton.clocks_before`,
  `newton.clocks_after` and `newton.timestep_check` in the step error. Check solver steps, clock and body state independently
  before changing the timing guard.
- [ ] Qualify the Isaac Sim, Newton, Warp, MuJoCo and GPU/driver versions that actually load. Document installation and
  diagnostics without replacing the simulator's matched packages. Inspect the version loaded inside Kit, not `pip show`.
- [ ] Exercise GPU Newton with representative rigid contacts, articulations, control, sensors, headless and windowed scenes,
  longer runs and the matching Isaac Lab version. Compare measured results with PhysX, including failures, and verify the
  actual solver device rather than CUDA availability alone.
- [ ] Identify any agent-facing Warp or Newton tools from real workflows and verified gaps. Reuse the existing scene, physics
  and Lab tools where they suffice; propose additions separately with clear inputs, device selection, resource limits and
  measured behaviour.
- [ ] Per-engine friction and solver profiles for the work cell, tuned on each engine (the current values are provisional).
- [ ] The sensors smoke check returned `PHYSICS_NOT_READY` once when it ran straight after the Newton checks in the same Kit;
  it passed on a fresh session. Reproduce it and find the cause.
- [ ] Run the scene reopen smoke check on both engines. It needs a saved scene opened by hand and has not been run yet.

### Work cell on Newton

The default engine is Newton, but the UR5e work cell (`workcell__cell` quick start and tower rebuild) is qualified on PhysX
only, and refuses on a Newton session with the message below and no engine switch. Get a PhysX session with
`run.sh --physics-backend physx`, `[sim] physics_backend = "physx"` or `TWINSMITH_PHYSICS_BACKEND=physx`.
`scripts/workcell_smoke.py` and the work-cell tests still qualify PhysX only.

Live results from a probe that bypassed the refusal (Isaac Sim 6.2.0.0, Newton 1.6.1, RTX 5080, headless, pack
`ur5e-jenga-workcell/v1`, 2026-11-13; run files outside this repository):

What works, once the scene is adapted in the session layer:

- [x] The bench's 385 triangle-mesh colliders (1.63 M triangles) overflow Newton's triangle-pair buffer ("7500052 > 1000000");
  convex hulls per bench mesh avoid it.
- [x] The RG2's passive joints author a drive with `maxForce` 0, which MuJoCo rejects ("actfrcrange[0] should be smaller than
  actfrcrange[1]"); removing those drives avoids it.
- [x] One articulation for the arm and gripper (the RG2 mount joint included in the arm, the RG2 articulation root removed)
  initializes. With the pack's separate RG2 articulation, Newton 1.6.1's USD importer lists the RG2 base in the arm's bodies and
  the forearm twice, skips `MjcEqualityWeldAPI` on the mount joint ("unresolved body target"), and MuJoCo fails with "mass and
  inertia of moving bodies must be larger than mjMINVAL".
- [x] The pack's UR5e and RG2 force drives (9400 N m per degree on the shoulder) are unstable in MuJoCo-Warp: the arm went
  non-finite and knocked the tower over 3 to 10 s in. Explicit gains (800/80, 800/80, 600/60, 200/20, 200/20, 100/10 N m/rad and
  N m s/rad; RG2 30/8 with its 1 N m limit) held the arm for 12 s.
- [x] The tower in the cell at 500 Hz with an elliptic friction cone, 100 iterations, contact gap 4 mm and `impratio` 10: max block
  drift 0.2 to 0.3 mm over 11 s after a 1 s settle (3.5 mm from the authored poses, mostly the 2 mm that the gaps from the
  per-block size variation close); 2150 of 2240 contact slots in use, no overflow. At 360 Hz 0.66 mm; at 250 and 120 Hz the tower
  collapses; without `impratio` 10 the top layer creeps 0.4 mm/s. The cell's tower needs the same 500 Hz as the generic one.

What does not work or is not yet done:

- [ ] The RG2 fingers do not move: `finger_joint` and `right_outer_knuckle_joint` targets of +0.3 and -0.5 rad left every gripper
  joint at 0.0000 after 100 steps. The closed chain, held by the excluded loop-closing revolute joints, is locked as converted.
  Needs a Newton-specific representation (equality or mimic constraints) and a measured finger width at open, mid and closed.
- [ ] The flange force and torque read: `get_link_incoming_joint_force` fails ("Failed to get incoming link joint forces from
  backend") without Newton's `link_incoming_joint_force` output enabled before initialization, which was not yet tried; the finger
  contact reads need Newton's contact snapshot instead of the PhysX contact view. Sensors keep reporting "unavailable" rather than
  inventing values.
- [ ] Per-engine friction profiles in `lab_hardware.json` (unconfirmed; start equal to the PhysX values).
- [ ] The robot-only gravity disablement for `mirror_real` on Newton (the PhysX rigid-body API is used today).
- [ ] The static check at rest (flange wrench equals the weight below the sensor) on both engines.
- [ ] Renders (cell, wrist, overhead) on Newton, and a PhysX re-check after the Newton changes.
- [ ] Then: remove the refusal in `workcell/scene.py`, make `workcell_smoke.py` take the engine, and cover both engines in the tests.

## Real-to-sim with RoboSimStudio

The `robosim` tool reproduced a real push on the GPU (Newton's MuJoCo-Warp solver) and on the CPU: the block's image displacement
matched the clip within the tool's tolerance. What it does not do yet:

- [ ] Match the block's rotation. Only the displacement of its centre is tuned and judged; the overlap at the end frame was 0.39.
- [ ] Fit mass and friction. The push path is tuned; the physics parameters are not.
- [ ] Estimate the camera pose from the clip. The simulated view is set by hand, so only the block's mask is compared.
- [ ] Take the pusher's path from the clip. `tune_push` searches a straight two-point path.
- [ ] Make `report` say when the file is complete; it returns `running` and the caller cannot tell.
- [ ] The RoboSimStudio viewer listens on every network interface and has no localhost-only option at the tested pin.
- [ ] A hand-written scene that keeps RoboSimStudio's default physics runs free bodies on its VBD solver on a GPU, which
  ignores the pusher. The tool reports this, but only the built-in template selects MuJoCo by itself.
- [ ] Repeat the GPU and CPU comparison over more clips and seeds; one pair was run for the T block.

## Release and compatibility

- [ ] Recheck guards and Jenga on both supported builds after the latest changes. Report failures separately; a passing host
  suite is not a live Sim test.
- [ ] Investigate the original 6.0.1 windowed PhysX step stall and fresh-Kit camera reuse; later non-reproduction is not a fix.
- [ ] Validate clean-machine setup, the full installer, external Lab discovery, and the supported ROS and display combinations
  before making broader compatibility claims.
- [ ] Re-run BrickSim on Isaac Sim 5.1 after the rename; it was skipped because its launcher has no headless option on the
  test PC.
- [ ] Run the work-cell smoke once from the new workspace layout (`<home>/robotics-workspace` with `assets/`); the work cell's
  asset-root lookup for it is covered by unit tests only.
- [ ] When a release is requested, review licensing and notices, name availability and PyPI installation using
  [publishing](PUBLISHING.md) and [provenance](VENDORING.md). A release states the engine and Isaac Sim build it was qualified on.

## Robotics and learning

- [ ] Jenga: define push and extraction, tower-stability and settling thresholds; evaluate scripted and learned policies across
  seeds and held-out geometry, poses and friction. Diagnose the failed pull before attempting grasp, extraction and a complete
  turn.
- [ ] BrickSim: reproduce expert and learned-policy evaluations across seeds with equal completed episodes. Compare with and
  without the simulator-only connection signal; measure and enforce realistic joint speeds before making transfer claims. Test
  varied brick poses and sizes.
- [ ] Mobile mission: reproduce station-to-station requests in fresh sessions, including blocked and unreachable goals. Record
  arrivals, retries and failures; label simulated loading and unloading.
- [ ] Preserve project checkpoints and configurations outside this repository. For demonstrations, record seeds, environment,
  backend, success denominators, failures and limitations; label scripted versus learned behaviour and simulation-only
  observations in any media.
- [ ] Add or improve a general public gripper workflow and command by width, force or contact. Assess suction or temporary
  attach and detach separately and label non-physical behaviour.
- [ ] Review remaining IK needs: collision awareness, straight-line behaviour and non-metre orientation weighting. Investigate
  smooth trajectories during play and policy velocity control.
- [ ] Extend live body interaction beyond the existing impulse option where needed; define PhysX and Newton behaviour and
  validate effects with a disposable scene.
- [ ] Consider sequential RL sweeps and add an `rl_games` preflight for minibatch divisibility and short runs that do not save
  checkpoints. Verify job-side timeout and exit-code reporting.

## Assets and workspace

- [ ] Investigate named presets and project asset discovery without widening approved roots. Keep format support on the existing
  import tool; see [asset import](ASSET-IMPORT.md).
- [ ] Untrusted asset isolation and authenticated remote upload remain separate, unimplemented projects. Prove filesystem,
  network and plugin restrictions throughout inspection and loading, authenticated bounded transfers, and fail-closed behaviour
  before exposing either.
- [ ] Review asset import and export gaps and the need for CAD, splats or heightmaps against real workflows. Do not treat
  unsupported formats or partial conversions as complete.
- [ ] Investigate faithful MJCF tendon and tendon-actuator conversion on 5.1 and 6.2, including Panda gripper contact and
  coupling. Imports warn until the behaviour is verified or preserved.

## Tool surface and reliability

- [ ] Decide the seven shorter names proposed for listed tools during the rename (the list is in the rename pull request).
- [ ] Regroup the host modules into subpackages; the rename left the flat layout.
- [ ] Add live smoke checks for the `ros2`, `assets` and `screen` toolsets, and for the code, log and UI tools of `sim`;
  `scripts/smoke.py` has none.
- [ ] Inventory the registered tools, arguments, results, side effects and usage. Propose a keep, combine or remove list and a
  compatibility plan to the owner before changing public contracts.
- [ ] Review the work-cell tool descriptions, partial outcomes, confirmations and destructive effects. Keep existing behaviour
  compatible during any migration.
- [ ] Improve queued-call and process-death errors so they identify the operation and unknown effects; avoid retrying an
  in-flight mutation after a timeout.
- [ ] Review refusal ordering and diagnostics: invalid types and ranges, missing shared staging, unexpected parameters, and
  `TWINSMITH_SIM_MAX_INSTANCES=0`.
- [ ] Review independent light intensity and colour edits, bounded log filtering, stopped-body reads, LiDAR sample and
  `NOT_READY` descriptions, clock source selection, and flat-floor warnings.
- [ ] Review screenshot output-location guidance and object-pose descriptions.
- [ ] Decide whether read-only scene calls may safely omit stage and session IDs; preserve targeting guarantees for changes.
  Examine desktop dialogs, X11 targeting and other display backends.

## Documentation and validation

- [ ] Verify navigation yaw tolerance guidance, map rasterisation and open-floor `start` advice.
- [ ] Verify evaluation episode-count semantics, Lab video dependencies, LiDAR preset frequency, camera adoption after reopen,
  and how an agent adopts a URDF pack.
- [ ] Rerun the [example](EXAMPLES.md) prompts, including LiDAR, in representative disposable sessions.
- [ ] For each change, run the relevant local CI and document observed results and limitations; inspect any published
  screenshots and videos for private data and asset permissions.

## Possibly closed by later changes

These were open on the old checklist but name things the code no longer has. Close them, or restate them, when the issues are
made.

- Scope strict mode and required assets to the lab work cell without blocking public robots. The strict and offline asset settings
  were removed with the old asset model; packs are the only model now, and nothing is downloaded.
- Review work cell quick-start variants. The work cell's quick-start tools were replaced by the work cell's `workcell__cell`
  grouped tool.
