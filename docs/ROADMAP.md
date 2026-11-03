# Project status and roadmap

Last updated: 2026-11-03. The tool references describe the current interfaces, and [Verification](VERIFICATION.md) holds the
latest results. A checked item means work exists, not that every machine or version has been tested.

## Goal

Give an agent one `isaac-mcp` server to inspect and operate an authorized Isaac Sim scene, use Isaac Lab, and see the
result, while keeping workflows repeatable and easy for other people to set up. The UR5e cell is the first supported
robot; the generic tools work with other robots and scenes. Preserve the UR5e tools and their behavior when generalizing.

## What exists

- [x] Host MCP server, local Kit socket bridge, tool discovery, multi-bridge selection and the in-Kit panel.
- [x] Shared bridge ownership: packs share a Kit endpoint and unload independently.
- [x] Generic scene and asset pack: 48 tools for stages, prims, physics, joints, cameras, LiDAR, OmniGraph, occupancy maps,
  ROS 2 TF, Kit UI and trusted USD, URDF and USDZ projects. See [TOOLS-SCENE.md](TOOLS-SCENE.md).
- [x] UR5e pack: 23 tools for the workstation, objects, scene state, ROS graphs and viewport recording.
- [x] Desktop capture: screenshots, single and multi-window recording, window placement and clip joining, including on a locked desktop.
- [x] Opt-in X11 desktop input and managed simulator sessions.
- [x] Isaac Lab host pack: 12 tools for discovery, jobs, checkpoints and TensorBoard data, with guards against a running job or Kit bridge.
- [x] Code-only repository with a local asset database, project files and an asset doctor. See [ASSETS.md](ASSETS.md).
- [x] Installer that reuses an existing Isaac Sim, plus client configuration helpers, host wheel and sdist, and an unpublished npm launcher.
- [x] ROS 2 helpers: RViz config writer, TF doctor and DDS environment script. See [ROS2-RVIZ.md](ROS2-RVIZ.md).
- [x] Agent skill for Claude Code, Codex, OpenCode and Antigravity. See [AGENT-SKILLS.md](AGENT-SKILLS.md).
- [x] CPU-only CI for host tests, documentation, package builds and the npm dry run.

## Verification and limits

See the [latest live check](VERIFICATION.md#documentation-check-2026-11-03) for exact scope. On 2026-11-03 the host suite
passed (1968 passed, 7 skipped), and on 6.2.0.0 headless PhysX, `./verify.sh` from a clean clone, `smoke_tools.py` and `validate_scene_tools.py`
passed. A successful run is not proof of every physics invariant or of real-robot fidelity.

Known limits: `randomize_single_object` can fail with its fixed narrow placement strip. The GPU guards are per host process, and the
Kit guard checks only configured and advertised endpoints. Current end-to-end evidence is for the documented Ubuntu and
NVIDIA systems; other operating systems, GPUs and builds must be tested before claiming support. Open issues per build are
in the [compatibility matrix](COMPATIBILITY.md).

## Next work

### 1. Reliability

- [x] Make `./verify.sh` detect a checkpoint with dead asset references up front. See [the message](VERIFICATION.md#live-ur5e-baseline).
- [ ] Improve the `randomize_single_object` placement configuration without breaking existing defaults; test feasible
  and infeasible scenes.
- [ ] Investigate the `NoneType.GetCamera` viewport exception during stage reset without suppressing it. See
  [the reproduction](VERIFICATION.md#windowed-viewport-reset-comparison).
- [ ] Complete the final PhysX evidence review and the [unresolved fresh-Kit camera-reuse gap](evidence/project-usd-workstation/README.md).
- [ ] Re-run the windowed workflows listed as not exercised in the latest live check.

### 2. General Isaac Sim tools

- [x] Register the twin and other packs on one shared bridge.
- [x] Deliver project-folder USD discovery and import and generic scene tools.
- [ ] Authenticated remote upload, named presets and isolation for untrusted assets remain unavailable. See the
  [end-to-end plan](plans/ASSET-SCENE-END-TO-END.md) and [follow-ups](plans/SIM-TOOLS-FOLLOWUP.md).

### 3. Reproducibility and portability

- [ ] Expand the [compatibility matrix](COMPATIBILITY.md); test clean installs and representative workflows on each
  target before documenting it as supported. The full installer's download path has not been qualified on a clean machine.
- [x] Align the locked 6.2 NumPy version with the Isaac Sim kernel metadata. See the [compatibility evidence](evidence/compatibility-2026-10-31.md).
- [ ] Repeat representative Isaac Lab jobs with installations using the updated 6.2 lock.
- [ ] Investigate asyncio task re-entry and destroyed pending tasks seen during the 6.0 and 6.1 scene checks.
- [ ] Reproduce and address the 6.0.0.1 panel becoming undocked after an extension reload.
- [ ] Test Isaac Lab discovery with an installation outside the developer's layout.
- [ ] Record the task, seed, versions, command, configuration and artifacts needed to reproduce a Lab job; check what
  the upstream framework already records first.
- [ ] Review bridge trust boundaries and remote access before supporting shared or network-exposed deployments.

### 4. Release, when requested

The repository is public. Publishing packages requires the owner's instruction.

- [ ] Resolve the upstream `omni-kit-mcp` license question and review third-party notices. See [VENDORING.md](VENDORING.md).
- [ ] Decide the supported release scope and versions, and recheck package-name availability.
- [ ] Test PyPI and npm installation paths, following [PUBLISHING.md](PUBLISHING.md).
- [ ] Publish the evidence release and demo video only after the [media checks](media/README.md#video-access-and-publication).

## Documents and entry points

See the [documentation index](README.md) for every page and script.
