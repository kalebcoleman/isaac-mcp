# Working checklist

This is the single list of proposed and unfinished work. Check an item only after verifying it
against the current code and the relevant Sim/Lab build. Current behavior and tool arguments
are in the [documentation index](README.md). Earlier plans and test records remain in Git history.

Keep the MCP focused on reusable capabilities. Small Jenga and warehouse quick starts remain;
substantial tasks, policies, missions, checkpoints and run data belong in separate projects.
Preserve existing tool contracts and user assets when making changes. Do not start or stop
unrelated jobs; record the source, versions, backend, observed effects and limits of a test.

## Release and compatibility

- [x] Fix the Sim 5.1 MJCF SO-101 and Panda failures; retest both arms and the floating-base Go2.
- [ ] Recheck guards and Jenga on both supported builds after the latest changes. Report failures
  separately; a passing host suite is not a live Sim test.
- [ ] Investigate the original 6.0.1 windowed PhysX step stall and fresh-Kit camera reuse;
  later non-reproduction is not a fix. Newton remains experimental.
- [ ] Validate clean-machine setup, the full installer, external Lab discovery, and the supported
  ROS/display combinations before making broader compatibility claims.
- [ ] When release is requested, review licensing and notices, name availability, and PyPI/npm
  installation using [publishing](PUBLISHING.md) and [provenance](VENDORING.md).

## Newton and Warp GPU physics (separate follow-up PR)

- [ ] Investigate the Sim 6.2 Newton exact-step timing mismatch: 20 requested steps at
  `dt=0.002` advanced the reported clock by 0.020 s, not 0.040 s. Check solver steps,
  clock and body state independently before changing the timing guard.
- [ ] Qualify the Isaac Sim, Newton, Warp, MuJoCo and GPU/driver versions that actually load;
  document installation and diagnostics without replacing the simulator's matched packages.
- [ ] Exercise GPU Newton with representative rigid contacts, articulations, control, sensors,
  headless and windowed scenes, longer runs and the matching Lab version. Compare measured
  results with PhysX, including failures, and verify actual solver device rather than CUDA availability alone.
- [ ] Identify any agent-facing Warp/Newton tools from real workflows and verified gaps. Reuse
  existing scene, physics and Lab tools where they suffice; propose additions separately with
  clear inputs, device selection, resource limits and measured behavior.

## Robotics and learning

- [ ] Jenga: define push/extraction, tower-stability and settling thresholds; evaluate scripted
  and learned policies across seeds and held-out geometry, poses and friction. Diagnose the
  failed pull before attempting grasp, extraction and a complete turn.
- [ ] BrickSim: reproduce expert and learned-policy evaluations across seeds with equal completed
  episodes. Compare with and without the simulator-only connection signal; measure and enforce
  realistic joint speeds before making transfer claims. Test varied brick poses and sizes.
- [ ] Mobile mission: reproduce station-to-station requests in fresh sessions, including blocked
  and unreachable goals. Record arrivals, retries and failures; label simulated loading/unloading.
- [ ] Preserve project checkpoints and configurations outside this repository. For demonstrations,
  record seeds, environment, backend, success denominators, failures and limitations; label
  scripted versus learned behavior and simulation-only observations in any media.
- [ ] Add or improve a general public gripper workflow and command by width/force/contact. Assess
  suction or temporary attach/detach separately and label nonphysical behavior.
- [ ] Review remaining IK needs: collision awareness, straight-line behavior and non-metre
  orientation weighting. Investigate smooth trajectories during play and policy velocity control.
- [ ] Extend live body interaction beyond the existing impulse option where needed; define
  PhysX/Newton behavior and validate effects with a disposable scene.
- [ ] Consider sequential RL sweeps and add an `rl_games` preflight for minibatch divisibility
  and short runs that do not save checkpoints. Verify job-side timeout and exit-code reporting.

## Assets and workspace

- [ ] Scope strict mode and required assets to the lab twin without blocking public robots;
  keep project roots explicit and shared consistently by host and Kit.
- [ ] Investigate named presets and project asset discovery without widening approved roots.
  Keep format support on the existing import tool; see [asset import](ASSET-IMPORT.md).
- [ ] Untrusted asset isolation and authenticated remote upload remain separate, unimplemented
  projects. Prove filesystem/network/plugin restrictions throughout inspection and loading,
  authenticated bounded transfers, and fail-closed behavior before exposing either.
- [ ] Review asset import/export gaps and the need for CAD, splats or heightmaps against real
  workflows. Do not treat unsupported formats or partial conversions as complete.
- [ ] Investigate faithful MJCF tendon and tendon-actuator conversion on 5.1 and 6.2, including
  Panda gripper contact and coupling. Imports warn until the behavior is verified or preserved.

## Tool surface and reliability

- [ ] Inventory actual registered tools, arguments, results, side effects and usage. Propose a
  keep/combine/remove list and compatibility plan to the owner before changing public contracts.
- [ ] Review twin quick-start variants and descriptions, partial outcomes, confirmations and
  destructive effects. Keep existing behavior compatible during any migration.
- [ ] Improve queued-call and process-death errors so they identify the operation and unknown
  effects; avoid retrying an in-flight mutation after a timeout.
- [ ] Review refusal ordering and diagnostics: invalid types/ranges, missing shared staging,
  unexpected parameters, and `ISAAC_MCP_SIM_MAX_INSTANCES=0`.
- [ ] Review independent light intensity/color edits, bounded log filtering, stopped-body reads,
  LiDAR sample/`NOT_READY` descriptions, clock source selection, and flat-floor warnings.
- [ ] Review screenshot output-location guidance and twin-only object-pose descriptions.
- [ ] Decide whether read-only scene calls may safely omit stage/session IDs; preserve targeting
  guarantees for changes. Examine desktop dialogs, X11 targeting and other display backends.

## Documentation and validation

- [ ] Verify navigation yaw tolerance guidance, map rasterization and open-floor `start` advice.
- [ ] Verify evaluation episode-count semantics, Lab video dependencies, LiDAR preset frequency,
  camera adoption after reopen, and how an agent adopts a URDF pack.
- [ ] Rerun README prompts, including LiDAR, in representative disposable sessions.
- [ ] For each change, run relevant local CI and document observed results and limitations;
  inspect any published screenshots and videos for private data and asset permissions.
