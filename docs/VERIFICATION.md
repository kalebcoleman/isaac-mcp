# Verification

The automated host suite and live simulator checks cover different things. A passing host suite does not establish
that Isaac Sim, the renderer, ROS, or a particular robot work on a machine. Record the commit, machine, driver,
versions, commands, and any warnings when reporting a result.

For installation identities, lock-preserving commands and evidence-reporting fields, use the
[reproducibility guide](REPRODUCIBILITY.md). For a non-destructive first connection, use the
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
plus the documentation check and `npm pack --dry-run` on pushes and pull requests. It does not launch Isaac Sim, train a policy, or test real
hardware. A workflow is only verified on GitHub after its run actually completes.

## Live UR5e baseline

These checks cover the complete lab scene, so they need [your own lab files](ASSETS.md#public-files-and-lab-files); without them `verify.sh`
reports the missing gripper and bench. Kit uses the asset folder next to its checkout unless `ISAAC_MCP_ASSET_PATH` says otherwise.
These checks modify the stage and move a simulated joint. Use a disposable simulator session and a free port, and
confirm which bridge is selected before running them. This example reuses an already installed Sim 6.2 interpreter;
set the path for your machine or omit `ISAAC_MCP_PYTHON` to use this repo's `.venv`.

```bash
export ISAAC_MCP_PYTHON=/path/to/isaacsim-env/bin/python
export UR5E_DT_PORT=18766
./run.sh --headless --port "$UR5E_DT_PORT"
# In another terminal after the app is ready, with the same environment:
./verify.sh
"$ISAAC_MCP_PYTHON" scripts/smoke_tools.py
```

`verify.sh` checks the workstation baseline: UR5e, RG2, QuickChanger and Vention scene prims; observed
robot-chain rigid bodies with gravity disabled; the six expected joint limits; and USD drive stiffness 100,
damping 10 and max force 200 (the project's default drive values). It commands the elbow by -0.1 rad and requires
the achieved displacement and return error to be within 0.02 rad, with finite observed joint positions. Joint limits
and drive attributes use a 0.01 tolerance. These are regression checks for this shipped configuration, not a
general test for arbitrary robots or proof of hardware fidelity; intentionally different drive settings require a
different expected baseline.

The motion check schedules a bounded Kit coroutine through the existing persistent
`run_python` session, then polls for its measured result. It awaits ordinary Kit frames;
it does not call nested `app.update()`. It requires the reopened stage to be stopped,
waits for a previous native Stop to settle, and refuses stage/timeline changes,
non-finite joints or a stalled clock. Use exclusive control of this disposable scene
until the check finishes: the bridge's per-call admission lock does not cover the
whole scheduled coroutine. A lost host connection does not automatically cancel a
Kit task; the check also has its own 90-second and 12,000-frame bounds.

The check exports a missing reference checkpoint from the current stage, checks that the exported file uses relative
asset references, and requests an asynchronous reopen. A later scene check verifies the expected prims are present;
the `opening` result itself only means the open was scheduled. A regenerated checkpoint's SHA-256 can differ between
machines and is **informational**, not a required match. The checks do not prove every physical behavior, camera,
ROS topic, or visual result. `scripts/smoke_tools.py` exercises additional twin tools and also rebuilds the stage;
review its output for warnings and its documented exclusions.

To check a different installed release, run `scripts/smoke_tools.py --sim-version 6.1` with a host Python that can
import the MCP package and `UR5E_DT_PORT` pointing at the selected disposable session. The option changes only the
expected app-version prefix; it does not establish compatibility or change the simulator installation. Check the
package version separately: the app's reported version may omit a fourth version segment. See
[compatibility](COMPATIBILITY.md) for the results obtained so far.

The validator is [`scripts/validate_ur5e_check.py`](../scripts/validate_ur5e_check.py). It rejects missing,
malformed, non-finite or out-of-range observations, and `verify.sh` exits nonzero if a check or its response fails.
If a check fails, inspect the named result and the simulator log before changing limits.

**If `verify.sh` stops with `FAILED: checkpoint`**, the git-ignored `checkpoints/ur5e-dt-workstation-person-view-portable-*.usda`
it would reuse references files that no longer exist, usually because it predates the code-only asset migration. Reopened,
`/World/UR5e` would be empty and the motion check would fail with `'NoneType' object has no attribute 'is_homogeneous'`.
The verifier checks this before touching the scene (`scripts/check_checkpoint_refs.py`) and never changes the file. Inspect
it, move it aside and re-run: `./verify.sh` exports a new one from your current assets. A clean clone is not affected.

## Live checks

<a id="last-live-check"></a><a id="headless-physx-check"></a>
On 2026-11-03, headless PhysX checks on Sim 6.2 passed `verify.sh`, the 20-check twin
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
  if OMNI_KIT_ACCEPT_EULA=YES "$ISAACSIM_PYTHON" scripts/validate_generic_scene.py > "$validation_log" 2>&1; then
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
- For ROS transforms, use `scripts/verify_tf.sh` with the matching `config/ros_dds.env` profile.
- Before a training run, obtain operator authorization and check GPU capacity. Use the
  [Isaac Lab guide](TOOLS-ISAACLAB.md) for status, metrics, and guard behavior.
- See [working checklist](CHECKLIST.md) for remaining work.
