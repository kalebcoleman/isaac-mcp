# Verification

The automated host suite and live simulator checks cover different things. A passing host suite does not establish
that Isaac Sim, the renderer, ROS, or a particular robot work on a machine. Record the commit, machine, driver,
versions, commands, and any warnings when reporting a result.

## Host checks

From the repo root, run:

```bash
cd mcp
uv run --isolated --python 3.12 --with-editable . --extra test --extra isaaclab pytest -q
uv build
```

From the repository root, `python3 scripts/check_docs.py` checks relative file links, fenced JSON examples and the
registered tool names in the three tool references. It does not check external URLs, link fragments, parameter
semantics, or every prose claim.

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

`verify.sh` checks the bundled workstation baseline: UR5e, RG2, QuickChanger and Vention scene prims; observed
robot-chain rigid bodies with gravity disabled; the six expected joint limits; and USD drive stiffness 100,
damping 10 and max force 200 (the project's default drive values). It commands the elbow by -0.1 rad and requires
the achieved displacement and return error to be within 0.02 rad, with finite observed joint positions. Joint limits
and drive attributes use a 0.01 tolerance. These are regression checks for this shipped configuration, not a
general test for arbitrary robots or proof of hardware fidelity; intentionally different drive settings require a
different expected baseline.

The check exports a missing reference checkpoint from the current stage, checks that the exported file uses relative
asset references, and requests an asynchronous reopen. A later scene check verifies the expected prims are present;
the `opening` result itself only means the open was scheduled. A regenerated checkpoint's SHA-256 can differ between
machines and is **informational**, not a required match. The checks do not prove every physical behavior, camera,
ROS topic, or visual result. `scripts/smoke_tools.py` exercises additional twin tools and also rebuilds the stage;
review its output for warnings and its documented exclusions.

The validator is [`scripts/validate_ur5e_check.py`](../scripts/validate_ur5e_check.py). It rejects missing,
malformed, non-finite or out-of-range observations, and `verify.sh` exits nonzero if a check or its response fails.
If a check fails, inspect the named result and the simulator log before changing limits.

## Last live check

On 2026-10-30, the verifier passed on Ubuntu 24.04 / RTX 5080, NVIDIA driver 580.178.04, and Isaac Sim 6.2.0.0 in a
disposable headless session. Changing `wrist_1_joint` damping from 10 to 4 caused the validator to fail with the joint
and field named; the value was restored. The simulator also logged the previously observed nested `app.update()`
warning; the broader rare-hang investigation remains open.

In a 6.2 session with the shared-bridge implementation in `382d2cb`, port 18766 served all 23 UR5e tools and two
temporary autoloaded packs, one of them policy-bearing. Both packs reloaded and remained callable when the twin was
disabled and re-enabled; all 23 twin tools returned. In a separate session, the policy-bearing pack was reloaded,
`./verify.sh` passed, and `scripts/smoke_tools.py` passed 20/20 checks. The latter still reported the known narrow-strip
warning for `randomize_single_object`. These checks exercised simulated scene behavior, not real hardware or every ROS,
windowed, GPU, OS, or Sim-version combination. No training was run. The generic `sim.*` pack remains a design and was
not part of these tests.

A separate direct Kit launch, without the `run.sh` deferral handshake, used `ISAAC_MCP_PORT=0` and
`UR5E_DT_PORT=18766`. Its bridge status reported port 18766 and discovery returned all 23 twin tools. The ROS library
environment was supplied for this check, but ROS topics and the broader twin smoke test were not exercised in that
direct-launch session.

With `./run.sh --headless --port 0`, a separate headless session bound an OS-assigned port and advertised it in the
runtime portfile. All 23 twin tools were present before and after disabling and re-enabling the twin on that endpoint.

## Other checks

- For a windowed workflow, confirm the intended window and capture with the [host tools](TOOLS-HOST.md).
- For ROS transforms, use `scripts/verify_tf.sh` with the matching `config/ros_dds.env` profile.
- Before a training run, obtain operator authorization and check GPU capacity. Use the
  [Isaac Lab guide](TOOLS-ISAACLAB.md) for status, metrics, and guard behavior.
- See [HANDOFF.md](HANDOFF.md) for earlier dated lab and home results, and [ROADMAP.md](ROADMAP.md) for remaining work.
