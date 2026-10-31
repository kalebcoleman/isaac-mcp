# Project status and roadmap

Last updated: 2026-10-30. This is the project-level overview; the tool references describe the current interfaces and
the design documents contain the background. A checked item means work exists, not that every machine or version has
been tested.

## Goal

Give an agent one `isaac-mcp` server to inspect and operate an authorized Isaac Sim scene, use Isaac Lab, and see the
result, while keeping workflows repeatable. The UR5e cell is the first supported robot; general scene tools and other
robots are intended additions. Preserve the current UR5e tools and their behavior when generalizing the bridge.

## What exists

- [x] Host MCP server, local Kit socket bridge, tool discovery, multi-bridge selection, and the in-Kit panel.
- [x] Shared bridge ownership: the UR5e pack and other registered packs can share a Kit endpoint and unload independently.
- [x] UR5e tool pack: 23 tools for the robot's workstation, objects, scene state, ROS graphs and viewport recording.
- [x] Desktop capture: screen and window screenshots, recordings, window listing and targeting.
- [x] Isaac Lab host pack: 12 tools for installation and task discovery, jobs, checkpoints and TensorBoard data.
- [x] Job guards for another Lab job and a reachable Kit bridge; explicit override flags are available.
- [x] Installer, client configuration helpers, host wheel/sdist and an unpublished npm launcher.
- [x] The October 2026 audit fixed verifier interpreter and exit-status handling, corrected the npm source, clarified
  host installation, and stopped the installer from overwriting an unrelated ffmpeg executable.

## Verification and limits

The 2026-10-30 GitHub Actions run for `66e84b4` passed 421 host tests, the documentation checks, Python package builds,
and the npm package dry run. An isolated host wheel installation and npm launcher fetch also worked in the prior audit. On the
home Ubuntu 24.04 / RTX 5080 machine, a headless Isaac Sim 6.2 run passed the UR5e smoke script's 20 checks and
registered 23 twin tools; the Lab install probe found Isaac Lab 3.0 and the custom UR5e reach task. Earlier checks
included windowed workflows and short training; see [TOOLS-ISAACLAB.md](TOOLS-ISAACLAB.md). The shared-bridge changes
passed 481 local host tests and a new live UR5e baseline; no training was run.

The UR5e verifier now checks explicit scene, gravity, joint-limit, drive and motion baselines, but a successful run is
not proof of every physics invariant or real-robot fidelity. A live Sim 6.2 run and a deliberately incorrect drive value
were checked on the 5080 on 2026-10-30; see [VERIFICATION.md](VERIFICATION.md). `randomize_single_object` can fail
with its fixed narrow placement strip. The GPU guards are per host process and the Kit guard only checks configured
and advertised endpoints.

The full twin launcher and environment are pinned to Isaac Sim 6.2.0.0 / Python 3.12, and current end-to-end evidence is
for the documented Ubuntu/NVIDIA systems. The host server is separate from the simulator, but other operating systems,
GPUs, Isaac Sim versions and installation layouts must be tested before claiming support. A prior isolated test of the
generic bridge in Sim 5.1 is not proof that the complete twin and Lab stack support 5.1.

## Next work

### 1. Reliable baseline and documentation

- [x] Make the UR5e verifier assert meaningful scene, physics, drive and motion invariants; keep informational checks
  such as regenerated checkpoint checksums clearly separate.
- [x] Add a CPU-only CI workflow for host tests, package builds and documentation checks. The first run on `66e84b4`
  passed. The live-simulator checklist is in [VERIFICATION.md](VERIFICATION.md).
- [ ] Run and record the live-simulator baseline for changes to the bridge or twin, including the commit, machine and
  dependencies. Expand the checklist as hardware and platform coverage grows.
- [ ] Improve the `randomize_single_object` placement configuration without breaking existing defaults; test feasible
  and infeasible scenes.
- [ ] Reconcile design notes as implementation evolves; treat the current tool references as the API
  source of truth.

### 2. General Isaac Sim tools without breaking the twin

- [x] Register the twin and other packs on one shared bridge. A temporary pack coexisted with the twin in a live 6.2
  session, including a pack reload and twin disable/enable; see [VERIFICATION.md](VERIFICATION.md).
- [ ] Verify each proposed API against a live headless Isaac Sim before implementation. Selected stage/prim inspection
  building blocks passed in a non-robot 6.2 scene; other APIs and versions remain open. The
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
- [ ] Check downstream consumers without overwriting their local changes. Update them deliberately after the server
  changes are verified.
- [ ] Review bridge trust boundaries and remote access before supporting shared or network-exposed deployments.

### 4. Release, when requested

- [ ] Resolve the upstream `omni-kit-mcp` permission/license question and review third-party assets and notices; see
  [VENDORING.md](VENDORING.md).
- [ ] Decide the supported release scope, metadata and versions, and recheck package-name availability.
- [ ] Test PyPI/npm installation paths and follow [PUBLISHING.md](PUBLISHING.md). Publishing and making the repository
  public require the owner's instruction. Use branches and pull requests for code review.

## Documents

- [UR5e tool reference](TOOLS-UR5E.md), [host and bridge tool reference](TOOLS-HOST.md), and
  [Isaac Lab tool reference](TOOLS-ISAACLAB.md): current tools and calling examples.
- [Architecture](ARCHITECTURE.md) and [MCP server setup](MCP-SERVER.md): processes, connection and client configuration.
- [Isaac Lab design and implementation notes](ISAACLAB-TOOLS.md): history and job-guard details.
- [Generic Sim tool draft](GENERIC-SIM-TOOLS.md): future design, not an implementation.
- [Verification](VERIFICATION.md): automated and live checks, expected values, limits and the last live result.

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
