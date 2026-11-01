# Verification

The automated host suite and live simulator checks cover different things. A passing host suite does not establish
that Isaac Sim, the renderer, ROS, or a particular robot work on a machine. Record the commit, machine, driver,
versions, commands, and any warnings when reporting a result.

## Host checks

From the repo root, run:

```bash
cd mcp
uv run --isolated --python 3.12 --with-editable . --extra test --extra isaaclab --extra scene-test pytest -q
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

The first completed run for commit `66e84b4` passed on 2026-10-30: 421 tests, documentation checks, wheel and sdist
builds, and the npm package dry run. [View the run](https://github.com/kalebcoleman/isaac-mcp/actions/runs/41170715313).
On the same date, the shared-bridge implementation in commit `382d2cb` passed 481 local host tests, the documentation
checks, the wheel and sdist builds, and the npm package dry run.

## Live UR5e baseline

These checks modify the stage and move a simulated joint. Use a disposable simulator session and a free port, and
confirm which bridge is selected before running them. This example reuses an already installed Sim 6.2 interpreter;
set the path for your machine or omit `UR5E_DT_PYTHON` to use this repo's `.venv`.

```bash
export UR5E_DT_PYTHON=/path/to/isaacsim-env/bin/python
export UR5E_DT_PORT=18766
./run.sh --headless --port "$UR5E_DT_PORT"
# In another terminal after the app is ready, with the same environment:
./verify.sh
"$UR5E_DT_PYTHON" scripts/smoke_tools.py
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

## Last live check

### Windowed viewport reset comparison

On 2026-10-31, the same windowed reset sequence was run three times each against base `d238989d` and PR head
`0e3521c7` on Ubuntu 24.04.5, RTX 5080, NVIDIA driver 580.178.04 and Python 3.12.3. Both used Isaac Sim 6.2.0.0,
Kit `110.4.0+feature.386251.187cf21b.gl`, `omni.kit.widget.viewport-110.0.1`,
`omni.kit.viewport.utility-2.1.2` and `ur5e-dt-1.0.0`, with ROS 2 Jazzy in isolated domain 197.

Each cycle started the UR5e/workstation scene, added FMB1 objects and their pose publisher, played and stopped the
timeline, started and stopped a 10-fps viewport recording, loaded Simple Room, deleted the objects, called
`ur5e_dt.new_stage`, and rebuilt the UR5e/workstation scene. Immediately before reset, the active camera was the
valid `/World/person_view`, the `/workspace_camera_sim` publisher was active, recording was stopped and the timeline
was stopped. The [original complete traceback](evidence/viewport-reset-2026-10-31.log) is preserved with its local
installation prefix replaced by `<isaacsim>`.

Both revisions logged the same `NoneType.GetCamera` exception once on each of the three resets, inside the viewport
extension while the old stage was being destroyed. After every reset, the active camera was a valid
`/OmniverseKit_Persp` and viewport frame-change events continued. After each rebuild, the active `/World/person_view`
was valid, rendered viewport captures succeeded, a separate ROS subscriber received a 1280-pixel camera image, and a
test cube dropped under gravity. After the third rebuild on each revision, selecting the workspace and person cameras
produced distinct rendered images. This supports recovery for this sequence and machine; it is not proof that all
stages, GPUs, recording states or Isaac versions recover. The exception is not suppressed, and its underlying cause
remains [tracked in the roadmap](ROADMAP.md#1-reliable-baseline-and-documentation).

The comparison also found that the existing real-time recorder sent every viewport redraw to ffmpeg: a roughly
one-second recording at 10 fps produced an 11.8-second file on the base and an 8.8-second file on the original PR
head. With the recording pacing change in this PR, three windowed 10-fps recordings each encoded to 1.0 second for
1.06–1.17 seconds of measured time including bridge calls and finalization. A windowed 30-fps recording encoded to
2.03 seconds for 2.09 seconds measured through the stop request. After this change, three more reset/rebuild cycles
also recovered rendered frames, valid cameras, ROS camera images and falling-cube physics. These are local simulated
checks, not a guarantee for all rendering rates or machines.

For the frame-scheduling regression, a controlled 10-second, 31-Hz input to a 30-fps encoder produced 299 distinct
frames out of 300 after the fix; the previous scheduling produced 155. This check decoded the MP4 and does not replace
a live viewport test at that exact rendering rate.

On 2026-10-31, a windowed Isaac Sim 6.2.0.0 session on Ubuntu 24.04.5 / RTX 5080 with driver 580.178.04 used the
MCP twin driver tool to start the system ROS 2 Jazzy UR driver in mock-hardware mode in isolated domain 197. Both
the joint-state broadcaster and scaled trajectory controller were active; a ROS `FollowJointTrajectory` request
succeeded and changed the mock elbow joint state from 0.0 to 0.05 rad. The MCP tool stopped the driver and status
returned `driver NOT running` and `controller_manager not reachable`. No physical robot was used. This verifies a
mock ROS controller and joint states, not end-to-end tracking of a physical robot.

On 2026-10-30, the verifier passed on Ubuntu 24.04 / RTX 5080, NVIDIA driver 580.178.04, and Isaac Sim 6.2.0.0 in a
disposable headless session. Changing `wrist_1_joint` damping from 10 to 4 caused the validator to fail with the joint
and field named; the value was restored. The simulator also logged the previously observed nested `app.update()`
warning; the broader rare-hang investigation remains open.

In a 6.2 session with the shared-bridge implementation in `382d2cb`, port 18766 served all 23 UR5e tools and two
temporary autoloaded packs, one of them policy-bearing. Both packs reloaded and remained callable when the twin was
disabled and re-enabled; all 23 twin tools returned. In a separate session, the policy-bearing pack was reloaded,
`./verify.sh` passed, and `scripts/smoke_tools.py` passed 20/20 checks. The latter still reported the known narrow-strip
warning for `randomize_single_object`; its headless run also produced an MP4 using the twin's real-time viewport
recording path. These checks exercised simulated scene behavior, not real hardware or every ROS,
windowed, GPU, OS, or Sim-version combination. No training was run. The generic `sim.*` pack was still a design at that source revision and was
not part of these tests; current implementation evidence is linked below.

A separate direct Kit launch, without the `run.sh` deferral handshake, used `ISAAC_MCP_PORT=0` and
`UR5E_DT_PORT=18766`. Its bridge status reported port 18766 and discovery returned all 23 twin tools. The ROS library
environment was supplied for this check, but ROS topics and the broader twin smoke test were not exercised in that
direct-launch session.

With `./run.sh --headless --port 0` (when the launcher enabled the twin by default), a separate headless session bound an OS-assigned port and advertised it in the
runtime portfile. All 23 twin tools were present before and after disabling and re-enabling the twin on that endpoint.

## Generic scene API validation

For the current MCP implementation and its separate live results, see [project scene evidence](evidence/project-scene/README.md). The historical standalone check below exercises selected underlying Kit/USD
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
expectation printed the assertion and exited with status 1. See [the design observations](GENERIC-SIM-TOOLS.md#324-first-live-scene-inspection-observations)
for the implications and remaining gaps. This is one version and machine, not a portability claim.

## Other checks

- For a windowed workflow, confirm the intended window and capture with the [host tools](TOOLS-HOST.md).
- For ROS transforms, use `scripts/verify_tf.sh` with the matching `config/ros_dds.env` profile.
- Before a training run, obtain operator authorization and check GPU capacity. Use the
  [Isaac Lab guide](TOOLS-ISAACLAB.md) for status, metrics, and guard behavior.
- See [ROADMAP.md](ROADMAP.md) for remaining work.
