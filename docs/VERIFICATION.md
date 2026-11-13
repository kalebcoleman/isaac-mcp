# Verification

The automated host suite and the live simulator checks cover different things. A passing host suite does not establish
that Isaac Sim, the renderer, ROS, or a particular robot work on a machine. This page is the test matrix: for each toolset,
the CPU checks that run in CI and on any machine, the live smoke check that needs a simulator, what each proves, and whether it
needs a GPU. Record the commit, machine, driver, versions, commands and any warnings when reporting a result; the
[last section](#recording-a-run-so-it-can-be-reproduced) says how. For a non-destructive first connection use the
[setup check](LINUX-SETUP.md#connect-and-check-without-changing-the-scene).

The physics engine matters to every live result. The target engine and the current default are in
[compatibility](COMPATIBILITY.md#target-engine). Every live smoke below takes `--backend newton` or `--backend physx`,
verifies the engine the simulator reports before it runs, and refuses a mismatch with exit 2. Each result records the engine,
the Isaac Sim build and the loaded Newton version when there is one.

## CPU checks

These need no simulator and no GPU. Run them from the repository root; CI runs the same ones.

```bash
python3 scripts/check_docs.py
python3 scripts/check_assets.py
python3 scripts/check_portable_paths.py
python3 scripts/check_private_paths.py
python3 scripts/gen_host_catalog.py --check
python3 scripts/gen_tool_reference.py --check
cd mcp
uv run --locked --isolated --python 3.12 --extra test --extra isaaclab --extra scene-test pytest -q
uv build
```

- `check_docs.py` checks relative links, fenced JSON examples, that every tool name a page names is registered (and every
  `group(action)` has that action), that retired phrases do not return, and that the generated reference and count blocks are
  current. It does not check external URLs or prose claims.
- `test_environment_docs.py` (in the suite) checks that [ENVIRONMENT.md](ENVIRONMENT.md) lists exactly the variables the code reads.
- The `scene-test` extra installs standalone `usd-core` into the isolated **host test** environment. Do not add it to a Sim
  environment as a substitute for Kit's USD bindings.
- The workflow in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs these on pushes and pull requests. It does not
  launch Isaac Sim, train a policy, or test hardware. A CI job that did not start because of billing or spending limits did not
  run; that is not a test failure, and a report should say so.

## Test matrix

Live smokes are run with `python3 scripts/smoke.py <toolset>`; `python3 scripts/smoke.py --list` lists every check with its GPU
and simulator needs without connecting to anything. A smoke mutates the selected simulator, so use a disposable one on its own
port. The runner verifies the engine before each check, prints a results table, writes a JSON report to a temporary folder (or
`--output-dir`), and stops after the first failure. `--check <name>` runs one check; it can be repeated.

| Toolset | CPU proof (pytest, from `mcp/`) | Live smoke | Proves | GPU |
|---|---|---|---|---|
| all | `test_toolsets`, `test_tool_listing`, `test_measure_tool_list`, `test_check_docs` | none | Every tool is in exactly one toolset; each toolset builds a server alone; off means no tools registered | no |
| `core` | `test_gateway_*`, `test_status_and_help`, `test_host_catalog_gateway`, `test_offline_builtins`, `test_tools_list_changed`, `test_bridge_*`, `test_job_health_and_script_jobs` | `smoke.py core --port 8766 --second-port 8767` | Two simulators stay isolated; a write aimed at the wrong one is refused | yes, two simulators |
| `scene` | `test_scene_prim_edit`, `test_scene_queries`, `test_scene_timeline`, `test_sim_quick_start`, `test_stage_open_unsaved`, `test_session_layer_authoring`, `test_scene_reopened` | `smoke.py scene --port 8766 --discard-changes --directory <dir>` | Quick start, create, edit, revert, clear and a generic read-only scene succeed. The reopen check needs a fresh process with the saved scene (`--saved`, `--body`, `--expected-z`) | yes |
| `physics` | `test_scene_joints`, `test_arm_ik`, `test_arm_path`, `test_controller_run`, `test_scene_step_failures`, `test_scene_physics_adapters`, `test_trajectory_replay`, `test_newton_parity` | `smoke.py physics --backend physx --port 8766 --discard-changes`, and again with `--backend newton` | A joint move converges and clamps; exact steps return the expected time; edge cases are refused. `newton_checks` runs only on Newton (on PhysX it is skipped, with a note) | yes |
| `sensors` | `test_scene_camera_*`, `test_scene_sensor_*`, `test_scene_lidar_save`, `test_scene_viewport`, `test_sensors_viewport` | `smoke.py sensors --port 8766` | A fresh camera picture after motion, and a non-empty LiDAR scan | yes |
| `ros2` | `test_ros2_tools`, `test_graph_and_map_tools`, `test_graph_build_and_stage`, `test_mesh_and_map`, `test_ros_env`, `test_nav2_params_message` | none registered; use the [TF doctor](ROS2-RVIZ.md#3-check-the-tree) against a quick-start robot | `/tf` and `/joint_states` publish | yes |
| `assets` | `test_asset_import`, `test_asset_packs`, `test_pack_manifest`, `test_project_assets`, `test_urdf_*`, `test_mjcf_*`, `test_sdf_urdf`, `test_xacro_lite`, `test_usdz`, `test_import_hardening` | none registered | An import's prim exists and has no composition errors (checked by hand) | yes |
| `lab` | `test_isaaclab_*` | `smoke.py lab` (runs `scripts/smoke_test.py`: an environment check and a short record on Cartpole; `--no-lab` skips Lab) | Both jobs end with exit code 0. The task chooses its own engine, so `--backend` is refused | yes, a Lab job |
| `screen` | `test_screen_*`, `test_contact_sheet`, `test_frames_video`, `test_capture_grid` | none registered; take one `screenshot` on a desktop session | A file is written | no for the tools; the desktop session needs a display |
| `code` | `test_run_python`, `test_sim_reload`, `test_tool_packages` | none registered; `run_python` returns a value and a file round-trips | | yes |
| `kit_debug` | `test_sim_ui`, `test_scene_advanced` | none registered; tail the Kit log | | yes |
| `workcell` | `test_workcell_*`, `test_lab_hardware_config` | `smoke.py workcell --port 8766 --config <absolute-config> --mutate-stage` (delegates to `scripts/workcell_smoke.py`; PhysX only) | Stopped-stage authoring and BOM validation, not dynamics or renders | yes |
| `robosim` | `test_robosim_bridge` | none registered | | no |
| `input`, `launch` | `test_desktop_input`, `test_sim_sessions`, `test_launch_script_newton` | manual, on request only | | yes for `launch` |
| BrickSim | `test_sim_compat`, `test_sim_versions`, the external-bridge cases in `test_sim_sessions` | the launch line in [BRICKSIM.md](BRICKSIM.md) on an Isaac Sim 5.1 BrickSim environment, then `status`, `sim__stage(action="info")` and one body-state read | `status` reports app version 5.1.0 and the tools answer. Not yet verified live since the toolsets change | yes |

Prerequisites that still apply to individual checks:

- The scene checks use the fixture folder made by `scripts/make_scene_fixture.py`; approve it as a project root first. Each
  validator's docstring in `scripts/smoke/` states its own prerequisites (an explicit Kit script opt-in, a stopped fresh Sim, a
  fixture name).
- Clear requires the stage left by the equivalents check. Reopen requires a fresh process with the saved scene. Prim-edit needs
  `--directory` for its fixture. Generic scene replaces the stage and requires `--discard-changes`.
- The work-cell check requires `--config` (absolute on the Kit host) and `--mutate-stage`, and rebuilds the work cell; do not run
  it against a scene you need to keep. `--discard-changes` is allowed only with explicit authorization, because the smoke
  replaces the stage.
- The Lab smoke starts GPU jobs rather than using the selected bridge, so its engine and build are unavailable in the report.
  Isaac Lab jobs refuse to start beside a running Kit bridge unless you knowingly pass `allow_concurrent_gpu=true`.
- A GPU run does not share a GPU with another job. Check what is running first; say so in one line before a long run.

## If a simulation call stops returning

The original 6.0.1.0 windowed PhysX stall remains unexplained; two later replays did not reproduce it. A timeout or closed
socket does not cancel a native call. Stop dependent mutations and follow the
[recovery guidance](TOOLS-SCENE.md#bounds-and-security-limits) instead of blindly retrying. Expected failures in a negative
test are not successful scene loads; unexplained missing or corrupt assets need investigation.

## How the smoke runner behaves

Existing fixture prerequisites still apply. Use repeated `--check` to select individual validators.
The runner starts each check's own MCP server with the toolsets that check needs (`TWINSMITH_TOOLSETS`,
see `CHECK_TOOLSETS` in `scripts/smoke.py`; `--toolsets` overrides it for every check) and calls tools by
their listed names. It waits, up to 60 s, for the simulator's reported engine to settle on the requested
one before it refuses a mismatch with exit 2 (the bridge answers `status` a few seconds before Newton is the
active engine). Validators that work on one shared stage (tools, equivalents, edges, sensors, camera
freshness, clear) run together in that order, each prerequisite added when a later check is selected, with
only the timeline stopped between them; every other check starts on a new empty stage with the timeline
stopped. Sessions needs `--second-port`; prim-edit needs `--directory` for its existing fixture. Generic scene
replaces the stage and requires `--discard-changes`. Reopen needs a simulator that already has a saved scene
open (the launcher cannot open a file at startup): without `--saved`, `--body` and `--expected-z` it is
reported as `needs_setup` with the exact commands. Workcell delegates unchanged to `workcell_smoke.py` and
needs `--config` and `--mutate-stage`; it remains PhysX-only. Lab delegates to `smoke_test.py` (started with
`default,+lab`), which starts GPU jobs rather than using the selected bridge; its backend/build are
unavailable in the report and `--backend` is refused rather than claiming that the task engine was verified.
Unmapped toolsets list no checks. A failed check is reported with its innermost exception and traceback and
the run continues with the next independent check; a check whose prerequisite did not pass is `not_run`,
and a backend mismatch stops the run.

## Records of earlier runs

<a id="last-live-check"></a><a id="headless-physx-check"></a>
On 2026-11-03, headless PhysX checks on Sim 6.2 passed the 20-check smoke of the work cell that preceded the work cell, and generic scene checks with the stated test setup. They do not establish all tools,
versions, windowed workflows, hardware or backends.

LiDAR save and reopen behaviour and recovery are described in [scene tools](TOOLS-SCENE.md#saved-lidar-scenes-and-scan).

<a id="windowed-viewport-reset-comparison"></a> On 6.2, a windowed reset sequence logged `NoneType.GetCamera` while destroying the
old stage. The tested sequence subsequently rendered and received a ROS camera image; its underlying cause remains open. This
does not prove every scene or configuration recovers.

### Generic scene API validation

For the current MCP implementation, see [scene tools](TOOLS-SCENE.md). The standalone check below exercises selected underlying Kit/USD
APIs in a **new headless process** with a synthetic scene containing Xforms, cubes, a sphere, a scope and an inactive
prim. It creates a new stage in that process and closes the app afterwards; it does not connect to or alter an existing
simulator session or train a policy. Starting Isaac Sim uses the
machine's normal graphics and GPU resources.

From the repository root, use an interpreter with Isaac Sim installed and an accepted EULA:

```bash
(
  ISAACSIM_PYTHON="/path/to/isaacsim-env/bin/python"
  validation_log=$(mktemp "${TMPDIR:-/tmp}/twinsmith-generic-scene.XXXXXX") || {
    printf 'Could not create validation log.\n' >&2
    exit 1
  }
  if OMNI_KIT_ACCEPT_EULA=YES "$ISAACSIM_PYTHON" scripts/smoke/validate_generic_scene.py > "$validation_log" 2>&1; then
    validation_status=0
  else
    validation_status=$?
  fi
  if [ "$validation_status" -eq 0 ]; then
    if ! grep '^GENERIC_SCENE_VALIDATION=' "$validation_log"; then
      printf 'Validation success marker missing.\n' >&2
      validation_status=1
    fi
  else
    printf 'Validation process failed (exit %s).\n' "$validation_status" >&2
  fi
  printf 'Full log: %s\n' "$validation_log"
  exit "$validation_status"
)
```

The command preserves a nonzero simulator exit status and also fails if the marked JSON line is missing; it prints the
log location in either case. The explicit `exit` stays inside the subshell. The script reports assertion failures with a
nonzero exit status, while Kit controls startup failures. The JSON lists the checks, observed helper behavior, Isaac Sim
distribution version, Python version and platform. The check validates selected building blocks
for `stage.info`, `stage.tree`, `prim.get` and `prim.find`; it does not test the registered MCP tools, an MCP bridge, deep-tree
limits, physics, capture, real hardware, or every stage and installation type. Isaac's deprecated `core.utils` helpers
are optional observations; the USD checks run directly against the active Kit context.

On 2026-10-30, the check passed on a working branch based on `584aa31`, using Ubuntu 24.04.5, kernel 7.0.0-38,
Python 3.12.3, Isaac Sim 6.2.0.0, an RTX 5080 and NVIDIA driver 580.178.04. The anonymous stage had a nonempty `anon:`
URL. The helper rejected the USD `Cube` type filter; it returned no matches for an anchored expression and for an
invalid expression, while a simple alternation matched. A separate run with an intentionally wrong default-prim
expectation printed the assertion and exited with status 1. This is one version and machine, not a portability claim.

## Other checks

- For a windowed workflow, confirm the intended window and capture with the [host tools](TOOLS-HOST.md).
- For ROS transforms, use `scripts/ros2_tf_doctor.py` with the matching `config/ros_dds.env` profile.
- Before a training run, obtain operator authorization and check GPU capacity. Use the [Isaac Lab guide](TOOLS-ISAACLAB.md)
  for status, metrics, and guard behaviour.
- See the [open items](OPEN-ITEMS.md) for remaining work.

## Recording a run so it can be reproduced

A useful report identifies **source, environment, execution and observed effects**. A lockfile or a passing
host suite alone does not establish native simulation behavior.

Keep logs and generated scenes outside Git. From the checkout root, these read-only commands
record source and simulator package identity without importing Kit or running a CUDA kernel:

```bash
git rev-parse HEAD
git rev-parse HEAD^{tree}
git status --short
sha256sum pyproject.toml uv.lock
uv --version
uname -srmo
cat /etc/os-release
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
printf 'DISPLAY=%s XDG_SESSION_TYPE=%s\n' "${DISPLAY:-unset}" "${XDG_SESSION_TYPE:-unset}"
SIM_PYTHON="${TWINSMITH_SIM_PYTHON:-$PWD/.venv/bin/python}"
"$SIM_PYTHON" -c 'import importlib.metadata as m, sys; print(sys.executable); print(sys.version); print({n:m.version(n) for n in ("isaacsim", "isaacsim-kernel", "numpy", "torch", "mcp")})'
uv pip check --python "$SIM_PYTHON"
uv tool list
```

For the host, retain `uv pip freeze --python <host-python>` and `uv pip check --python <host-python>`
using the interpreter of the installed uv tool (located under `uv tool dir`). Record Lab and
external ROS in their own interpreters/terminals. Do not use a system ROS Python environment
to stand in for Sim's bundled bridge.

| Record | Include |
|---|---|
| Source | Full commit/tree, dirty diff, input hashes, any diagnostic launcher deviation |
| Setup | Exact commands, tool/interpreter versions, exit codes, cache use and fresh versus existing environment |
| Runtime | Sim package and app/Kit build, backend, headless/windowed, GPU/driver/display, selected PID/port/session/stage |
| Effects | Requested operation, tool result, independent state/pixel/physics observations, source preservation |
| Outcome | Attempted/pass/fail/pending/unsupported; warning/error counts with the precise log interval |
| Media | Original hashes, decoded frame count versus visually sampled frames, redaction and asset notices |
| Recovery | Response/timeout, subsequent named health checks and observed process exit; cleanup is not recovery |

A timeout or client disconnect does not prove native cancellation. A successful import does
not prove physics readiness. A successful save is not a solver checkpoint: reopen in a fresh
process and verify the properties and effects you need; see [save contracts](TOOLS-SCENE.md).

Before sharing evidence, replace personal paths with neutral placeholders and visually inspect
screenshots. Preserve original private records; publish a separate redacted summary with hashes.
Keep USD/user assets, large logs and videos out of Git. See [media publication](media/README.md#video-access-and-publication)
and [third-party notices](../THIRD-PARTY-NOTICES.md).
