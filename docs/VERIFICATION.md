# Verification

The automated host suite and live simulator checks cover different things. A passing host suite does not establish
that Isaac Sim, the renderer, ROS, or a particular robot work on a machine. Record the commit, machine, driver,
versions, commands, and any warnings when reporting a result.

To record an installation and a result so that someone else can reproduce it, see
[the last section](#recording-a-run-so-it-can-be-reproduced). For a non-destructive first connection, use the
[setup check](LINUX-SETUP.md#connect-and-check-without-changing-the-scene). The suites below have
different scopes; native verifiers can build scenes and move joints.

## If a simulation call stops returning

The original 6.0.1.0 windowed PhysX stall remains unexplained; two later replays did not
reproduce it. A timeout or closed socket does not cancel a native call. Stop dependent
mutations and follow the [recovery guidance](TOOLS-SCENE.md#bounds-and-security-limits)
instead of blindly retrying. Expected failures in a negative test are not successful scene
loads; unexplained missing or corrupt assets need investigation.

## Host checks

From the repo root, run:

```bash
cd mcp
uv run --locked --isolated --python 3.12 --extra test --extra isaaclab --extra scene-test pytest -q
uv build
```

From the repository root, `python3 scripts/check_docs.py` checks relative file links, fenced JSON examples and the
registered tool names in the four tool references, agent tier counts, skill tool names, and private-path bytes. It does not check external URLs, link fragments, parameter
semantics, or every prose claim.

The `scene-test` extra installs standalone `usd-core` into the isolated **host test** environment.
Do not add it to a Sim environment as a substitute for Kit's USD bindings. From the repository root,
`python3 scripts/check_assets.py` checks bundled asset licences and
`python3 scripts/gen_host_catalog.py --check` detects packaged twin-catalog drift.

The GitHub Actions workflow in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs these CPU-only checks
plus the documentation check on pushes and pull requests. It does not launch Isaac Sim, train a policy, or test real
hardware. A workflow is only verified on GitHub after its run actually completes.

## Live UR5e baseline

Use [`scripts/workcell_smoke.py`](../scripts/workcell_smoke.py) with an already-running,
explicitly selected disposable Kit bridge and an absolute lab config path on the Kit host.
It requires `--port`, `--config` and `--mutate-stage`; it rebuilds the workcell, so do not
run it against a scene you need to preserve. It checks the ready/stopped PhysX report,
units, gravity, Jenga dimensions/counts and part metadata. It does not replace the old
workstation verifier’s joint-motion or checkpoint checks. See [asset packs](ASSET-PACKS.md) for lab files.

## Live checks

Use `python3 scripts/smoke.py all --list` to list checks and their GPU/simulator
requirements without connecting to anything. The entry point is
`python3 scripts/smoke.py <toolset> --host <host> --port <port> --backend physx`
(or `--backend newton` for an explicitly requested Newton check). It verifies the
selected engine before each live check, refuses a mismatch with exit 2, and prints
a results table and the path to a JSON report in a temporary directory. Use a host
Python environment with this checkout's dependencies. No simulator is started by
the scene runner; checks mutate an explicitly selected disposable bridge.

Existing fixture prerequisites still apply. Use repeated `--check` to select individual validators.
The runner starts each check's own MCP server with the toolsets that check needs (`ISAAC_MCP_TOOLSETS`,
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

<a id="last-live-check"></a><a id="headless-physx-check"></a>
On 2026-11-03, headless PhysX checks on Sim 6.2 passed the legacy workstation verifier, the 20-check twin
smoke, and generic scene checks with the stated test setup. They do not establish all tools,
versions, windowed workflows, hardware or backends.

LiDAR save/reopen behavior and recovery are described in [scene tools](TOOLS-SCENE.md#saved-lidar-scenes-and-scan).

<a id="windowed-viewport-reset-comparison"></a> On 6.2, a windowed reset sequence logged
`NoneType.GetCamera` while destroying the old stage. The tested sequence subsequently rendered
and received a ROS camera image; its underlying cause remains open. This does not prove every
scene or configuration recovers.

## Generic scene API validation

For the current MCP implementation, see [scene tools](TOOLS-SCENE.md). The standalone check below exercises selected underlying Kit/USD
APIs in a **new headless process** with a synthetic scene containing Xforms, cubes, a sphere, a scope and an inactive
prim. It creates a new stage in that process and closes the app afterwards; it does not connect to or alter an existing
simulator session or train a policy. It does not explicitly enable the UR5e extension. Starting Isaac Sim uses the
machine's normal graphics and GPU resources.

From the repository root, use an interpreter with Isaac Sim installed and an accepted EULA:

```bash
(
  ISAACSIM_PYTHON="/path/to/isaacsim-env/bin/python"
  validation_log=$(mktemp "${TMPDIR:-/tmp}/isaac-mcp-generic-scene.XXXXXX") || {
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
- Before a training run, obtain operator authorization and check GPU capacity. Use the
  [Isaac Lab guide](TOOLS-ISAACLAB.md) for status, metrics, and guard behavior.
- See [working checklist](CHECKLIST.md) for remaining work.

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
SIM_PYTHON="${ISAAC_MCP_PYTHON:-$PWD/.venv/bin/python}"
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
