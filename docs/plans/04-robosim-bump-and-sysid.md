# Plan 04: RoboSimStudio bump and `sysid`

Roadmap [Phase 4](../ROADMAP.md#phase-4-real-to-sim-and-system-identification). Two parts that can be two pull requests:
**A** moves the RoboSimStudio pin; **B** adds system identification. Neither needs a GPU. Independent of plan 03.

## Part A: bump the pin

**Problem.** `PIN = "87b5e32"` in `mcp/twinsmith/robosim/bridge.py`. Upstream `main` was `4e52e1a` on 2026-11-13, 13 commits
later. The wrapper calls the script `examples/live/ctl` in the checkout; upstream also has a module entry point,
`python -m robosimstudio_real2sim_agent`, which is the supported surface.

**Steps.**

1. In an updated checkout (the owner's, or a scratch clone with its own `uv sync`), read the 13 commits and list any that
   touch `examples/live/ctl`, `robosimstudio_real2sim_agent/`, `sim/scene.py` or `specs/arrangement.py`.
2. Compare `examples/live/ctl --help` with `python -m robosimstudio_real2sim_agent --help` at the new commit. If the module
   takes the same subcommands, switch the wrapper to it and delete the `ctl` path and its executable-bit check. If it does
   not, keep `ctl` and write down why in `docs/ROBOSIM.md`. Do not support both.
3. Run the CPU sequence from `docs/ROBOSIM.md` (template `tee`, `start` on `cpu`, `rollout`, `report`, `stop`) at the new
   commit. The push result must match the old pin's within the tool's own tolerance, and `push_applied.solver` must still be
   `SolverMuJoCo`.
4. Check whether the viewer gained a localhost-only option and whether `report` can now say when its file is complete; both
   are open items. Use them if present.
5. Set `PIN`, update the pin and every "pin 87b5e32" source note in `robosim/template.py` and `docs/ROBOSIM.md`.

**Files.** `mcp/twinsmith/robosim/bridge.py`, `template.py`, `mcp/tests/test_robosim_bridge.py`, `docs/ROBOSIM.md`,
`docs/OPEN-ITEMS.md`, `docs/ARCHITECTURE.md` (the pin table).

**Done when.** The host suite passes; the CPU sequence result is recorded in the pull request with both commits; a GPU run
is listed as not done unless the owner ran one.

## Part B: `sysid`

**Problem.** `robosim` compares images and does not fit mass or friction. Hand-set values are in use: the Newton probe held
the arm with gains chosen by trial (800/80, 800/80, 600/60, 200/20, 200/20, 100/10). MuJoCo ships an official system
identification module (`mujoco.sysid`): bounded parameters fitted to recorded trajectories by least squares over batched
rollouts.

**Version fact that shapes the design.** RoboSimStudio's project file notes MuJoCo 3.15 in its lock. The module is in the
MuJoCo repository alongside the 4.0.1 release (2026-11-11). So `sysid` cannot share RoboSimStudio's environment: it gets its
own. First step of the work: confirm which released wheel contains `mujoco.sysid` and pin that exact version.

### Design

One grouped tool, `sysid`, in the `real2sim` toolset, on the [wrapper contract](../ARCHITECTURE.md#wrapped-native-beside).
Its "checkout" is only an environment: `<workspace>/tools/sysid/.venv`, which the owner creates
(`uv venv` and `uv pip install mujoco==<pin>`; the tool prints these and never runs them). The pin is checked by asking that
interpreter for `mujoco.__version__`. If plan 03 has merged, use `sibling.py`; if not, this plan creates it as specified
there.

A fit reads one **dataset folder**, the contract for anything exported from the lab:

```text
<dataset>/
  model.xml            the MuJoCo model (MJCF)
  params.json          [{"name", "target", "nominal", "min", "max"}]
  episodes/
    0001.npz           time [T], ctrl [T, nu], sensordata [T, nsensor]
    0002.npz
  SOURCE.md            who recorded it, when, on what, and who approved the export
```

`target` names a model quantity by element and field, for example `joint:shoulder_pan:damping` or `geom:block:friction[0]`.
The folder must be inside an approved root. `SOURCE.md` must exist: a dataset without a recorded approval is refused.

| Action | Does |
|---|---|
| `status` | The environment, the MuJoCo version against the pin, recent fits |
| `fit` | A job: runs a small driver script with the environment's interpreter; writes `fit.json` (fitted values, bounds hit, residual before and after, per-episode error) into `<workspace>/runs/sysid/<name>/` |
| `report` | Reads a finished fit: the table of nominal, fitted and bounds, the error reduction, and a `held_out` score when `holdout` episodes were named |

The driver (`mcp/twinsmith/sysid_driver.py`) is the only file that imports `mujoco`, and only the sibling interpreter runs
it. Keep it close to the upstream module's own example; do not re-implement the optimiser.

**First targets**, in order: (1) synthetic recovery, to prove the pipeline; (2) the UR5e's joint damping, armature, friction
loss and position gains from recorded joint trajectories, which need no perception; (3) block mass and friction from push
recordings, after [Phase 5](../ROADMAP.md#phase-5-markerless-perception) provides object poses. Converting a ROS 2 bag into
`episodes/` is a separate offline script in the owner's project, not part of this tool: nothing here reads a robot.

### Files

| File | Change |
|---|---|
| `mcp/twinsmith/sysid.py` (new) | `PIN`, dataset checks, argument building, registration. About 200 lines |
| `mcp/twinsmith/sysid_driver.py` (new) | Runs in the sibling environment: load, fit, write `fit.json` |
| `mcp/twinsmith/toolsets.py`, `server.py` | `sysid` joins `real2sim`; register when on |
| `mcp/tests/test_sysid.py` (new) | Below |
| `docs/SYSID.md` (new), `docs/ROBOSIM.md`, `docs/README.md`, `docs/ENVIRONMENT.md`, `docs/OPEN-ITEMS.md` | The dataset contract, setup, limits; tick "Fit mass and friction" only for what a recorded fit shows |

### Tests

- Dataset checks: a missing `SOURCE.md`, a parameter with `min > max`, an episode whose arrays disagree in length, and a
  folder outside an approved root are each refused with a distinct code.
- Wrapper: with a fake interpreter, `fit` starts a job with the expected argument list; a version other than the pin gives
  `pin_match: false` and a warning; a missing environment gives setup guidance.
- **Synthetic recovery**, skipped unless `mujoco` at the pin is importable in the test environment (add a `sysid-test`
  extra; do not add `mujoco` to the default test extra): a one-joint pendulum model, episodes generated with damping 0.30
  and armature 0.010, a fit started from 0.10 and 0.002 recovers both within 5 percent, and the held-out error falls.
- `report` on an unfinished fit says `running`, on a finished one returns the table.

### Done when

- The CI commands in the [plans index](README.md) pass.
- The synthetic recovery test passes locally with the `sysid-test` extra, and its output is in the pull request.
- `docs/SYSID.md` states plainly what was fitted on real data: nothing, until the owner exports a dataset.

## Not in this pull request

Fitting on GPU through MuJoCo-Warp, writing fitted values into `lab_hardware.json` (a reviewed, separate change with the
dataset's `SOURCE.md` as its source), and RoboSimStudio's rotation, camera and pusher-path gaps.
