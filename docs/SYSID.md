# System identification (`sysid`)

`sysid` fits model parameters, such as joint damping, armature, friction loss, position gains and a geom's mass and
friction, to recorded trajectories. It uses MuJoCo's own system identification module, `mujoco.sysid`: bounded
parameters fitted by least squares over rollouts of the recorded commands. It runs on the CPU in its own Python
environment and needs no Isaac Sim or Kit bridge. The tool is in the `robosim` toolset, which is off by default
(`toolsets(action="enable", names=["robosim"])`).

**What has been fitted on real data: nothing yet.** Only the synthetic pendulum below has been fitted. A real fit waits
until the owner exports a dataset. Nothing here reads a robot: turning a ROS 2 bag into `episodes/` is an offline script in
the owner's project, not part of this tool.

## The environment

The host never imports `mujoco`. The fit runs in its own environment, by default `<workspace>/tools/sysid/.venv`
(`TWINSMITH_SYSID_ROOT` names another folder). The owner creates it; the tool prints these steps and never runs them:

```bash
uv venv --python 3.12 <workspace>/tools/sysid/.venv
uv pip install --python <workspace>/tools/sysid/.venv/bin/python "mujoco[sysid]==4.0.1"
```

**The pin is MuJoCo 4.0.1**, released 2026-11-11. `mujoco.sysid` ships in the `mujoco-core` 4.0.1 wheel. The `mujoco`
4.0.1 wheel is a meta-package that pulls in `mujoco-core` and `mujoco-studio`, and its `sysid` extra adds what the
module needs (`scipy`, `matplotlib`, `plotly` and others). Without that extra, `import mujoco.sysid` succeeds but provides
nothing, so `status` checks that `mujoco.sysid.optimize` exists. RoboSimStudio locks MuJoCo 3.15, so the two cannot
share an environment. `status` asks the environment's interpreter for `mujoco.__version__`. A different version
gives `pin_match: false` and a warning.

## The dataset

A fit reads one dataset folder. This folder is the format for anything exported from the lab:

```text
<dataset>/
  model.xml            the MuJoCo model (MJCF)
  params.json          [{"name", "target", "nominal", "min", "max"}, ...]
  episodes/
    0001.npz           time [T], ctrl [T, nu], sensordata [T, nsensor]; optional qpos [nq], qvel [nv]
    0002.npz
  SOURCE.md            who recorded it, when, on what, and who approved the export
```

- **Approval.** The folder must be inside a user-approved root, the same `approval.json` roots that
  [project imports](TOOLS-SCENE.md#project-usd-and-trusted-roots) use. Only the user can approve one. A refusal gives the
  command for the user to run. `SOURCE.md` must exist: a dataset without a recorded approval is refused.
- **Parameters.** `nominal` is where the fit starts. It must lie within `min` and `max`. `target` names one model
  quantity as `KIND:NAME:FIELD` or `KIND:NAME:FIELD[i]`:

  | Kind | Fields |
  |---|---|
  | `joint` | `damping`, `stiffness` (arrays; index 0 is the default), `armature`, `frictionloss` |
  | `geom` | `friction[0]` sliding, `[1]` torsional, `[2]` rolling; `mass` |
  | `actuator` | `kp`, `kv` of a position actuator (through `mujoco.sysid`'s `apply_pgain` and `apply_dgain`) |

  There is no body target: a body whose inertia comes from its geoms ignores its own `mass`, so a fit would change
  nothing. Fit the geom's `mass` instead.
- **Episodes.** Row `k` holds the time `t_k`, the command applied from `t_k` and the sensor reading at `t_k`. Row 0 is the
  reading at the initial state. `sensordata` columns are the model's sensors in order (`nsensordata` values). The initial
  state is `qpos` and `qvel` when given, else the model's `qpos0` at rest. The host reads only the arrays' shapes, from
  the `.npy` headers. The driver checks the column counts against the compiled model.

Each refusal has its own code: `ROOT_NOT_APPROVED`, `SOURCE_MISSING`, `PARAMS_INVALID` (for example `min > max`),
`EPISODE_INVALID` (for example arrays that disagree in length) and `DATASET_INVALID`.

## Actions

| Action | Does |
|---|---|
| `status` | The environment, its MuJoCo version against the pin, and the five newest fits |
| `fit` | Checks the dataset, then starts a job. `dataset` and `name` are required; `holdout` names episodes (file names without `.npz`) kept out of the fit and scored afterwards; `max_iters` (default 50) and `timeout_s` (default 3600) |
| `report` | `running` while the job runs. When `fit.json` exists: the table of nominal, fitted and bounds, `bounds_hit`, the residual before and after, per-episode errors and `held_out` |

`fit` copies `sysid_driver.py` into `<workspace>/runs/sysid/<name>/` as `driver.py`. It writes `request.json` with the
parsed parameters, the held-out episodes and a SHA-256 of every dataset file. Then it runs the driver with the
environment's interpreter as a [job](TOOLS-ISAACLAB.md): follow it with `jobs(action="status", job_id=...)` and cancel it
with `jobs(action="cancel")`. The job uses no GPU and hides CUDA. A fit name is used once; `FIT_EXISTS` refuses a second.

The driver follows `mujoco.sysid`'s own integration test. It builds one `ModelSequences` for the fit episodes, calls
`build_residual_fn`, then runs `optimize` with the `mujoco` least-squares backend. It writes `fit.json`, and through
`mujoco.sysid.save_results` the upstream files in `upstream/`: `params_x_hat.yaml` and the model with the fitted values.
The residual is `mujoco.sysid`'s normalised sensor residual: an RMS without units, per episode and overall.

## Verified

- **Synthetic recovery** (`test_sysid.py::test_synthetic_recovery`, run with the `sysid-test` extra): a one-joint pendulum.
  Three episodes were generated with damping 0.30 and armature 0.010, and the fit started from 0.10 and 0.002 with
  episode 3 held out. It recovered 0.3000 and 0.01000 (both within 1e-12). The residual fell from 0.057 to 9e-15, the
  held-out residual from 0.041 to 2e-14.
- The same fit through the tool against the environment at `<workspace>/tools/sysid` gave the same numbers.

Not yet done: a real dataset, the UR5e's joints, and block mass and friction from push recordings (after markerless
perception provides object poses). Fitting on the GPU through MuJoCo-Warp and writing fitted values into
`lab_hardware.json` are separate changes.
