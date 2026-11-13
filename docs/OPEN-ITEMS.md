# Open items

The unfinished work from the old working checklist, grouped by topic so each group can become an issue. Check an item only
after verifying it against the current code and the relevant Sim and Lab build. Current behaviour and tool arguments are in the
[documentation index](README.md).

Keep the MCP focused on reusable capabilities. Small Jenga and warehouse quick starts remain; substantial tasks, policies,
missions, checkpoints and run data belong in separate projects. Preserve existing tool contracts and user assets when making
changes. Do not start or stop unrelated jobs; record the source, versions, backend, observed effects and limits of a test.

## Newton qualification

Newton is the target engine and PhysX stays the default until these pass; the statement and the known limits are in
[compatibility](COMPATIBILITY.md#target-engine). Each result records the engine, the Isaac Sim build and the loaded Newton
version.

- [ ] Hold a full 54-block Jenga tower for ten seconds on Newton with the contact-buffer check on. The tower is not yet stable.
- [ ] Make a Franka joint move settle within the PhysX tolerance on Newton.
- [ ] Decide `sim__physics(action="raycast")` on Newton: implement a query, or keep it refused and say so.
- [ ] Run the work cell's quick start with a force and torque read on Newton. It is qualified on PhysX only.
- [ ] Record the live runs that show exact stepping, the one-off push and camera capture after motion passing on Newton; they
  are reported, not yet recorded in this repository. Run `scripts/smoke.py physics --backend newton --discard-changes
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
- [ ] When these pass: change the default engine in one place and remove the "until the Newton checks pass" clause from the
  [target engine](COMPATIBILITY.md#target-engine) statement.

## Release and compatibility

- [ ] Recheck guards and Jenga on both supported builds after the latest changes. Report failures separately; a passing host
  suite is not a live Sim test.
- [ ] Investigate the original 6.0.1 windowed PhysX step stall and fresh-Kit camera reuse; later non-reproduction is not a fix.
- [ ] Validate clean-machine setup, the full installer, external Lab discovery, and the supported ROS and display combinations
  before making broader compatibility claims.
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

- [ ] Inventory the registered tools, arguments, results, side effects and usage. Propose a keep, combine or remove list and a
  compatibility plan to the owner before changing public contracts.
- [ ] Review the work-cell tool descriptions, partial outcomes, confirmations and destructive effects. Keep existing behaviour
  compatible during any migration.
- [ ] Improve queued-call and process-death errors so they identify the operation and unknown effects; avoid retrying an
  in-flight mutation after a timeout.
- [ ] Review refusal ordering and diagnostics: invalid types and ranges, missing shared staging, unexpected parameters, and
  `ISAAC_MCP_SIM_MAX_INSTANCES=0`.
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

- Scope strict mode and required assets to the lab twin without blocking public robots. The strict and offline asset settings
  were removed with the old asset model; packs are the only model now, and nothing is downloaded.
- Review twin quick-start variants. The twin's quick-start tools were replaced by the work cell's `ur5e_dt__workcell`
  grouped tool.
