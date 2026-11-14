# Planning and evaluation: `gap` and `vabar`

Two wrapped tools, each in its own toolset and both off by default:

| Toolset | Tool | Wraps | Licence |
|---|---|---|---|
| `planning` | `gap` | [Graph-as-Policy](https://github.com/graph-robots/graph-as-policy): compiles a task into a typed skill graph and runs it in simulation | Apache-2.0 |
| `eval` | `vabar` | [VABAR](https://github.com/BerkeleyAutomation/VABAR): benchmark task variants and frozen cells on Newton through RoboSimStudio | Apache-2.0 |

Neither needs an Isaac Sim or Kit bridge. Each runs the upstream command line from a pinned checkout with that checkout's
own interpreter. twinsmith never imports, installs or updates either one, and copies nothing from them. Turn them on with
`toolsets(action="enable", names=["planning", "eval"])` or `TWINSMITH_TOOLSETS=default,+planning,+eval`.

## Setup (the owner runs this)

One Graph-as-Policy checkout serves both tools, at the commit VABAR 1.0 names. The three checkouts sit side by side under
`<workspace>/tools/`:

```text
<workspace>/tools/
  graph-as-policy/      KeplerC/graph-as-policy at 06dd6193e13eab816123febef1369deda0849d91
  open-robot-skills/    KeplerC/open-robot-skills at b15859a244658abc6060f3aa3b6827780746a5fe
  vabar/                BerkeleyAutomation/VABAR at 64b594ed2e1ae25e431d235de086c7cfd99d9aaf ("VABAR 1.0 release")
```

```bash
cd <workspace>/tools
git clone https://github.com/KeplerC/graph-as-policy.git
git -C graph-as-policy checkout 06dd6193e13eab816123febef1369deda0849d91
git -C graph-as-policy submodule update --init --recursive
git clone https://github.com/KeplerC/open-robot-skills.git
git -C open-robot-skills checkout b15859a244658abc6060f3aa3b6827780746a5fe
git clone https://github.com/BerkeleyAutomation/VABAR.git vabar
git -C vabar checkout 64b594ed2e1ae25e431d235de086c7cfd99d9aaf
```

Then create each environment with the steps in upstream's READMEs at those commits. Both need an NVIDIA GPU and CUDA:
Graph-as-Policy's base dependencies include cuRobo and `cuda-core[cu12]`, and VABAR's include `robosimstudio[gap,curobo]`
(about 12 GB). There is no CPU-only environment.

```bash
(cd graph-as-policy && uv sync && uv run gap skills install --all && uv run gap skills check --download)
(cd open-robot-skills/tools/curobo && CUDA_HOME=/usr/local/cuda uv sync)   # cuRobo tool bundle, once
(cd vabar && uv sync && uv run python -m vabar list && uv run python -m vabar warmup)
```

`gap skills check --download` needs `HF_TOKEN` (the SAM 3 weights are gated). VABAR's `warmup` compiles the renderer's
shader cache and Warp kernels once, in 5 to 8 minutes; a graph cell's first run fails without it. Graph-as-Policy asks
for 24 GB of GPU memory: run it on a PC that has that. `gap(action="status")` reports a smaller GPU rather than working
around it.

Other locations: `TWINSMITH_GAP_ROOT` and `TWINSMITH_VABAR_ROOT` (see [ENVIRONMENT.md](ENVIRONMENT.md)). The skill
library is always the `open-robot-skills` folder beside the Graph-as-Policy checkout.

## Paid model calls

`gap(action="generate")` asks a paid model (OpenRouter by default) to write a graph. It is refused unless the owner's
own file allows it:

```toml
# ~/.config/twinsmith/config.toml
[planning]
allow_paid = true
```

A project `twinsmith.toml` cannot set it. Without it, the tools also keep `OPENROUTER_API_KEY` and `GAP_VLM_*` from every
run, so graph nodes that ask a VLM fail instead of spending money; the answer says so. `HF_TOKEN` is always passed,
because model weights are free downloads. `status` reports whether each key is set and never its value.

## `gap`

| Action | Runs | Notes |
|---|---|---|
| `status` | Nothing upstream | Checkout, commit and pin; the skill library's commit and pin; GPU memory against the 24 GB requirement; whether `HF_TOKEN` and `OPENROUTER_API_KEY` are set; `allow_paid` |
| `tools` | `gap tools list --format json` | The skill catalog. Read-only |
| `check` | `gap check --format json` | Which tool bundles are ready here and which skills can run. Read-only; no `--probe`, so no model is called |
| `generate` | `gap generate <instruction> --out ...` | A job, no GPU. Needs `allow_paid`. The graph lands in `<workspace>/runs/gap/<name>/output/` |
| `run` | `gap run --sim <SUITE/TASK> ... <graph>` | A job that uses the GPU. `graph` is a workflow directory or `workflow.json` in `<workspace>/runs/gap/` (a relative path is read from there), the Graph-as-Policy checkout or the VABAR checkout. Traces go to `<workspace>/runs/gap/<name>/output/` |

At the pin, `gap check` takes no graph: it is the capability report. Graph validation (`gap run --validate-only`) is not
wrapped.

**Safety rule: twinsmith does not run Graph-as-Policy on real hardware.** Upstream's `gap run` has `--real franka` and
`--real ur_zed`. The tool has no argument that produces `--real`, no free-form argument and no `extra_args`; `sim` is
required for `run` and must be `SUITE/TASK`. Every value is checked for `--real` (or an abbreviation argparse would expand
to it) before anything else, and the built argument list is scanned again before the process starts. Either refusal is
`REAL_ROBOT_REFUSED`, "twinsmith does not run Graph-as-Policy on real hardware". The process never sees `ROS_DOMAIN_ID`
or any other `ROS_*` variable.

## `vabar`

| Action | Runs | Notes |
|---|---|---|
| `status` | Nothing upstream | Checkout, commit and pin; the commits and pins of the Graph-as-Policy and skill checkouts; whether `warmup` has run (its stamps in the renderer cache) |
| `list` | `python -m vabar list` | Every `suite/variant`. Read-only |
| `run` | `python -m vabar run --task <t> --solve routine\|graph\|idle [--graph <label>] [--episodes ...]` | A job that uses the GPU. `graph` is a label the task ships, such as `gt` |
| `bench` | `python -m vabar bench --cell <c> --solve ... [--limit n] --workers <1-4>` | `print_only=true` (the default) returns the command and starts nothing; `false` runs it as a job that uses the GPU |
| `results` | Nothing upstream | Reads every `episode_results.jsonl` in a run folder and returns successes per task and solver; without `run`, the newest run folders |

`warmup` and `studio` are not wrapped: the owner runs the one-time warmup, and `studio` serves a viewer on the network.
Run folders are `<workspace>/runs/vabar/<name>/`.

## Jobs, guards and answers

`generate`, `run` and `bench` return a `job_id` in the shared jobs folder: wait with `jobs(action="status", job_id=...,
wait_s=...)` and stop with `jobs(action="cancel")`. A job that uses the GPU is guarded as an Isaac Lab launch is: it is
refused beside another GPU job or a running Isaac Sim unless `allow_concurrent_gpu=true`, and past a configured GPU-hour
budget. Nothing is stopped to make room.

Every answer has the same envelope: `{ok, tool, action, commit, pin, pin_match, exit_code, result | error, warnings}`,
with `code` on a refusal (`REAL_ROBOT_REFUSED`, `PAID_NOT_ALLOWED`, `NOT_INSTALLED`, `PATH_NOT_ALLOWED`, `NOT_FOUND`,
`ALREADY_EXISTS`, `INVALID_VALUE`, `GUARD_REFUSED`, `UPSTREAM_FAILED`). A checkout at another commit still runs, with a
warning that it differs from the tested pin.

## What has been verified

The host tests run both tools against fake checkouts: the exact argument lists, the refusals, the scrubbed environment,
the paid-call switch and the jobs. With the real checkouts cloned at their pins and no environments, `status` reports the
pins and the missing environments. No Graph-as-Policy or VABAR process has run from these tools yet.
