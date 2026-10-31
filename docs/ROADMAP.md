# Project status and roadmap

Last updated: 2026-10-30. This is the project-level overview; the tool references describe the current interfaces and
the design documents contain the background. A checked item means work exists, not that every machine or version has
been tested.

## Goal

Give an agent one `isaac-mcp` server to inspect and operate an authorized Isaac Sim scene, use Isaac Lab, and see the
result, while keeping research repeatable. The UR5e cell is the first supported robot; general scene tools and other
robots are intended additions. Preserve the current UR5e tools and their behavior when generalizing the bridge.

## What exists

- [x] Host MCP server, local Kit socket bridge, tool discovery, multi-bridge selection, and the in-Kit panel.
- [x] UR5e tool pack: 23 tools for the robot's workstation, objects, scene state, ROS graphs and viewport recording.
- [x] Desktop capture: screen and window screenshots, recordings, window listing and targeting.
- [x] Isaac Lab host pack: 12 tools for installation and task discovery, jobs, checkpoints and TensorBoard data.
- [x] Job guards for another Lab job and a reachable Kit bridge; explicit override flags are available.
- [x] Installer, client configuration helpers, host wheel/sdist and an unpublished npm launcher.
- [x] UR5e experiments and their evidence moved to the separate `ur5e-digital-twin` workspace; this repo owns the
  server, bridge and tool packs. See the [README](../README.md) for repository boundaries.
- [x] The October 2026 audit fixed verifier interpreter and exit-status handling, corrected the npm source, clarified
  host installation, and stopped the installer from overwriting an unrelated ffmpeg executable.

## Verification and limits

The latest audit on 2026-10-30 ran the host suite: 392 tests passed; four additional installer tests were added after
that run began and passed separately. An isolated host wheel installation and npm launcher fetch also worked. On the
home Ubuntu 24.04 / RTX 5080 machine, a headless Isaac Sim 6.2 run passed the UR5e smoke script's 20 checks and
registered 23 twin tools; the Lab install probe found Isaac Lab 3.0 and the custom UR5e reach task. Earlier handoff
records document lab-workstation, windowed and short training checks. See [HANDOFF.md](HANDOFF.md) for their dates and
scope. The latest audit did not run training.

The verifier reports several observed values without asserting all their intended ranges; a successful run is useful
evidence but is not proof of every physics invariant. `randomize_single_object` can fail with its fixed narrow placement
strip. The GPU guards are per host process and the Kit guard only checks configured and advertised endpoints.

The full twin launcher and environment are pinned to Isaac Sim 6.2.0.0 / Python 3.12, and current end-to-end evidence is
for the documented Ubuntu/NVIDIA systems. The host server is separate from the simulator, but other operating systems,
GPUs, Isaac Sim versions and installation layouts must be tested before claiming support. A prior isolated test of the
generic bridge in Sim 5.1 is not proof that the complete twin and Lab stack support 5.1.

## Next work

### 1. Reliable baseline and documentation

- [ ] Make the UR5e verifier assert meaningful scene, physics, drive and motion invariants; keep informational checks
  such as regenerated checkpoint checksums clearly separate.
- [ ] Add automated host tests, package build and documentation checks in CI; maintain a live-simulator smoke checklist
  for changes to the bridge or twin. Record what was run, the commit, machine and dependencies.
- [ ] Improve the `randomize_single_object` placement configuration without breaking existing defaults; test feasible
  and infeasible scenes.
- [ ] Reconcile dated handoff and design notes as implementation evolves; treat the current tool references as the API
  source of truth.

### 2. General Isaac Sim tools without breaking the twin

- [ ] Resolve how a generic tool pack and the existing twin register on the same live bridge. The twin currently creates
  its own bridge, while the standard launcher disables the generic bridge's socket.
- [ ] Verify each proposed API against a live headless Isaac Sim before implementation; the
  [generic tools draft](GENERIC-SIM-TOOLS.md) is a design, not an installed tool pack.
- [ ] Implement and test general scene inspection, authoring, physics and capture in small stages. Re-run the UR5e
  baseline after changes and test at least one non-UR5e scene.

### 3. Reproducibility and portability

- [ ] Define a supported-version and platform matrix separately for the host, bridge, twin, desktop capture and Lab
  tools; test clean installs on each selected target before documenting them as supported.
- [ ] Make Isaac Lab discovery and setup work cleanly with user-provided installations; document required paths and
  task-module registration, and test an installation outside the developer's default layout.
- [ ] Record the task, seed, versions, command, configuration and artifact locations needed to reproduce a Lab job;
  check what the upstream framework already records before adding duplicate state.
- [ ] Check downstream consumers, including the twin submodule pin and BrickSim integration, without overwriting their
  local changes. Update them deliberately after the server changes are verified.
- [ ] Review bridge trust boundaries and remote access before supporting shared or network-exposed deployments.

### 4. Release, when requested

- [ ] Resolve the upstream `omni-kit-mcp` permission/license question and review third-party assets and notices; see
  [VENDORING.md](VENDORING.md).
- [ ] Decide the supported release scope, metadata and versions, and recheck package-name availability.
- [ ] Test PyPI/npm installation paths and follow [PUBLISHING.md](PUBLISHING.md). Publishing, making the repository
  public and opening a PR require the owner's instruction.

## Documents

- [UR5e tool reference](TOOLS-UR5E.md), [host and bridge tool reference](TOOLS-HOST.md), and
  [Isaac Lab tool reference](TOOLS-ISAACLAB.md): current tools and calling examples.
- [Architecture](ARCHITECTURE.md) and [MCP server setup](MCP-SERVER.md): processes, connection and client configuration.
- [Isaac Lab design and implementation notes](ISAACLAB-TOOLS.md): history and job-guard details.
- [Generic Sim tool draft](GENERIC-SIM-TOOLS.md): future design, not an implementation.
- [Handoff](HANDOFF.md): machine-specific setup and dated verification history.

## Repository entry points

Run these from the repository root unless noted. The default simulator launcher creates a UR5e session, so use a
separate port and avoid disturbing a user's existing scene when testing.

| Entry point | Purpose |
|---|---|
| `./install.sh --no-sim` | Install the host server; `--accept-eula` without `--no-sim` also installs the pinned Sim environment. Client-registration options are in the [README](../README.md). |
| `./run.sh --headless --port 8766` | Launch the UR5e Kit application and bridge; omit `--headless` for its window. `UR5E_DT_PYTHON` selects an existing compatible Python environment. |
| `./verify.sh` | Inspect a running UR5e scene and exercise a joint; it builds/reopens scene content. Set `UR5E_DT_PORT` to its bridge and `UR5E_DT_PYTHON` when not using the repo `.venv`. See the verification limits above. |
| `.venv/bin/python scripts/smoke_tools.py` | Exercise the twin's bridge tools against a running sim. This changes and rebuilds the stage, so use a disposable session. |
| `scripts/check_machine.sh` | Check the local OS, GPU, dependencies and environment for the pinned setup. |
| `scripts/catch_hang.sh [count] [timeout]` | Repeatedly boot and quick-start the twin to investigate the historical startup hang; uses the GPU. |
| `scripts/verify_tf.sh` / `scripts/tf_capture.py` | Inspect the twin's ROS transform topics with the matching DDS configuration. |
| `python -m isaac_mcp.client status --port 8766` | Query a running bridge from a Python environment with `isaac_mcp` installed. See [host and bridge tools](TOOLS-HOST.md) for MCP and CLI differences. |
| `mcp/examples/` | Examples for bridge integration and remote clients; not automatically registered as tools. |
| `tools/` | Asset conversion and repair utilities for the RG2; inspect inputs and outputs before use. |

`scripts/launch_ur_fake.sh` and `scripts/sim_bringup.sh` manage ROS driver processes and can affect the surrounding
robot environment. Use them only for the intended authorized setup; a simulation-only task does not require starting
a real robot driver. The `config/` directory contains the shared ROS/DDS profile and URSim configuration.
