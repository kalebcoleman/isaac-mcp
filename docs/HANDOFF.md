# Handoff — isaac-mcp (as of 2026-10-29)

Read this first when picking the project up on another machine or in a new chat. Everything described here is
committed to `main` of <https://github.com/kalebcoleman/isaac-mcp> (private). Plain facts; "verified" means it was run.

## 1. What this is

An MCP server for **Isaac Sim 6.2.0.0** and **Isaac Lab 3.0.0**, built on Aldrin Inbaraj's repos (`isaac-sim-mcp` is MIT; `omni-kit-mcp` has no upstream license file, see `docs/VENDORING.md`)
`omni-kit-mcp` and `isaac-sim-mcp` (pinned 9d0366b / 27f6100, see `docs/VENDORING.md`) plus Kaleb's screen tool.
Deliberately **separate from the `ur5e-digital-twin` research repo**. One name everywhere: **`isaac-mcp`** (no aliases;
Kaleb does not want legacy `ur5e-mcp` / `kit-mcp` / `twin-mcp` names).

| Layer | Path | Notes |
|---|---|---|
| Host MCP server (stdio) | `mcp/isaac_mcp/` | Python module `isaac_mcp`; screen tools, Kit bridge client, `isaaclab__*` tools |
| Kit bridge extension | `exts/isaac.mcp.bridge/` (+ `isaac.mcp.panel/`) | runs inside Isaac Sim |
| Tool pack: UR5e twin | `exts/ur5e-dt/` | 22 `ur5e_dt.*` tools (clients see `ur5e_dt__*`), 111 MB of assets |
| Tool pack: Isaac Lab | `mcp/isaac_mcp/isaaclab/` | 12 host-side `isaaclab__*` tools, design in `docs/ISAACLAB-TOOLS.md` |
| Launch/check | `install.sh`, `run.sh`, `verify.sh`, `scripts/` | |
| npm launcher | `npm/` | `npx` -> `uvx`; unpublished |

## 2. State: verified vs not

Verified on the lab box (RTX PRO 6000 Blackwell, driver 595.99.02, Ubuntu 22.04):
- 362 unit tests pass (`cd mcp && uv run --isolated --python 3.12 --with-editable . --extra test --extra isaaclab pytest -q`, ~90 s).
- Isaac Sim 6.2 boots headless in ~7 s and windowed; bridge lists 29 tools (22 `ur5e_dt.*`); `quick_start_with_workstation` works.
- TF graphs use `IsaacComputeTransformTree -> ROS2PublishTransformTree`; `/objects_poses_sim` and `/tf_cam` publish.
- Isaac Lab tools: a real 2-epoch `Isaac-Reach-UR10` rl_games run through `isaaclab__train` succeeded; status, checkpoint
  discovery and TensorBoard metrics read it back.
- Screen tools list the Isaac Sim / Isaac Lab window and auto-target it (was picking a Firefox tab). A windowed `isaaclab__play` job was found by pid, screenshotted and recorded through the MCP tools (job status reports `window`).
- Fresh-clone flow `./install.sh` -> `./run.sh --headless` -> `./verify.sh` passed (in the old repo layout; re-run once here).
- Client configs (Claude Code/Desktop, OpenCode, Antigravity) were switched to server `isaac` -> `~/.local/bin/isaac-mcp`.

Verified on the home box (RTX 5080 16 GB, driver 580.178.04, Ubuntu 24.04), 2026-10-29:
- `scripts/check_machine.sh` passes; Isaac Sim 6.2 boots headless in ~10 s and windowed (RTX Real-Time 2.0 renders the UR5e
  cell, no renderer crash); `quick_start_with_workstation` ~3 s.
- `scripts/catch_hang.sh 30`: no hang in 30 runs (3-5 s each), same as the lab box. The flake has not reproduced anywhere.
- Isaac Lab 3.0.0 at `~/Desktop/ur5e-twin-62/learning6` (a `consolidate-62` checkout); `isaaclab__envs_list` lists 206 envs
  incl. `UR5e-Cell-Reach-Direct-v0`.
- `./install.sh --no-sim --claude --isaaclab-python ... --isaaclab-root ...` registers `isaac` with the Lab env vars.

NOT verified: Isaac Lab training on the 5080 (no run started; ask first, 16 GB); the `uvx`/npm install paths from a
published package; the multi-job GPU contention.

## 3. Setting up on the RTX 5080 machine

```bash
gh auth login                                   # repo is private
git clone https://github.com/kalebcoleman/isaac-mcp.git && cd isaac-mcp
./install.sh --accept-eula                      # Isaac Sim 6.2 env (uv, ~20 GB, 10-20 min first time) + isaac-mcp CLI
scripts/check_machine.sh                        # must say "Machine check passed"
./install.sh --no-sim --claude                  # register server "isaac" in Claude Code + Desktop (restart Desktop)
# add --isaaclab-python <venv>/bin/python --isaaclab-root <IsaacLab dir> (or export ISAACLAB_PYTHON/ROOT first) to also
# write the Isaac Lab env vars into the client configs; without them the isaaclab__* tools only search the default path.
```
- **Driver.** Isaac Sim **5.1** segfaulted in `librtx.scenedb.plugin.so` on driver 595.x; 580.x was fine for 5.1 on the
  5080. **6.2 on a 5080 is untested.** `torch 2.11+cu130` needs a CUDA-13-capable driver (>= 580). If the renderer
  crashes, try headless first (`./run.sh --headless`); a windowed crash is a driver issue, not an MCP bug.
- **VRAM.** The 5080 has 16 GB: use small `num_envs` and run **one GPU job at a time** (a running Kit app plus a
  training job will contend).
- **EULA.** First `import isaacsim` blocks on NVIDIA's EULA prompt; `install.sh --accept-eula` / `OMNI_KIT_ACCEPT_EULA=YES`
  handles it. Anything that imports isaacsim without that var set will hang on stdin.
- **Display.** MCP screen tools need `DISPLAY` to match the desktop session (`echo $DISPLAY`; `:1` on the lab box). The
  installer writes `DISPLAY` and `UR5E_DT_PORT=8766` into client configs; fix them if the home session differs.
- **ROS/DDS.** The sim and every ROS client must source `config/ros_dds.env` (Cyclone, localhost-only). A mismatch
  silently publishes into the void. `run.sh` does this; `scripts/tf_capture.py` follows the same file.
- **Ports.** 8766 = twin bridge and `scripts/install.py` autostart default, 8767 = Isaac Lab bridge by convention
  (`mcp/examples/isaaclab_bridge_launch.py`), 8799/8793 = probe/diagnostic scripts. `run.sh` refuses a busy port.
  (Until 2026-10-29 the installer/Lab convention was 9009/9010; all docs, examples and `scripts/install.py` now use 8766/8767.)

## 4. Isaac Lab at home (needed for the Isaac Lab tools and for gap A below)

The tools locate an install via `ISAACLAB_PYTHON` (+ optional `ISAACLAB_ROOT`), else
`~/Desktop/ur5e-digital-twin/learning6/{.venv,IsaacLab}`. That environment is **not** in this repo (26 GB). To recreate:
1. Get the twin repo's `consolidate-62` branch (it tracks the 13 `learning6/` files incl. `ur5e_lab/`; `master` does not,
   and the local `migration/isaacsim6-isaaclab3` branch was never pushed).
2. `cd learning6 && uv sync --locked` (Isaac Lab 3.0.0, Isaac Sim 6.2.0.0, torch 2.11 cu130, rl_games pinned in `pyproject.toml`).
3. Clone NVIDIA Isaac Lab `v3.0.0` (commit `e3f7d9ff1f5d7f8280de3ece9000b7117f66bcf4`) to `learning6/IsaacLab` (git-ignored; the
   tools run `IsaacLab/scripts/reinforcement_learning/train.py` and `play.py`).
4. `export ISAACLAB_PYTHON=$PWD/.venv/bin/python ISAACLAB_ROOT=$PWD/IsaacLab`.

On the home 5080 box Isaac Lab already exists at `~/Desktop/ur5e-twin-62/learning6` (a `consolidate-62` checkout, 25 GB `.venv`),
not at the tools' default path; the `isaac` server in the client configs carries `ISAACLAB_PYTHON`/`ISAACLAB_ROOT` for it
(`./install.sh --no-sim --claude --isaaclab-python ~/Desktop/ur5e-twin-62/learning6/.venv/bin/python --isaaclab-root ~/Desktop/ur5e-twin-62/learning6/IsaacLab`).

Isaac Lab 3 facts the tools rely on (found by running, not documented elsewhere in this repo): no `--headless` flag
(`--viz none` = headless, `--viz kit` = window); RL backend via `--rl_library` (rl_games, rsl_rl, skrl, ...); extra args
are Hydra overrides (`agent.params.config.minibatch_size=1536`); rl_games needs `minibatch_size` to divide
`num_envs * 24`; runs land in `<IsaacLab>/logs/<lib>/<experiment>/<timestamp>/`, checkpoints in `nn/`.

## 5. Open work (priority order)

**A. `envs_list` and custom tasks** — DONE 2026-10-29 on the 5080: it already read `gymnasium.registry`; it now imports `ISAACLAB_TASK_MODULES` (+ `ISAACLAB_TASK_PATH`) and reports `import_errors` (see `docs/ISAACLAB-TOOLS.md`). Verified: `UR5e-Cell-Reach-Direct-v0` and a throwaway module list.

**B. `quick_start_with_workstation` hang flake.** *(2026-10-29, lab RTX PRO 6000: `scripts/catch_hang.sh 30` ran 30 boots + quick_starts with NO hang, 6-7 s each, so the flake is not a plain boot-then-call race; still worth one run on the 5080.)* Twice on 2026-10-29 the first call after boot never returned
(app log ends with PhysX warnings; earlier a "Nested app.update() deferred asyncio work" warning); normally ~5 s; never
reproduced since; trees were byte-identical. Run `scripts/catch_hang.sh 30` on the 5080: it loops boot+quick_start and,
on a hang, saves the log, a process snapshot, nvidia-smi and (if `py-spy` is installed) a stack dump to
`/tmp/isaac-mcp-hang/hang-<n>/`. Lead: the spin-wait on `app.update()` inside the extension (see `exts/ur5e-dt/ur5e_dt/extension.py`,
quick-start path). Do not use `scripts/probe_62.sh` for this (slow).

**C. Publish to PyPI / npm** — only Kaleb can (accounts/tokens). Checklist in `docs/PUBLISHING.md`.

**D. More tools.** Done 2026-10-29: windowed play + screenshot/video loop; a job guard so two Isaac Lab jobs do not start together
(`allow_concurrent` on train/eval/play, see `docs/ISAACLAB-TOOLS.md` section 8). Still open: (1) refuse to start a training job while a
Kit bridge (the sim) is running (documented in section 8 as design only; not implemented); (2) the generic Isaac Sim tool pack, whose
design draft is `docs/GENERIC-SIM-TOOLS.md` (24 `sim.*` tools; the APIs were grounded by grep only, verify on a live headless sim; biggest
open issue: a sibling pack cannot reach the live socket because the twin builds its own `McpBridge()` and `run.sh` sets `ISAAC_MCP_PORT=0`).

**E. Cleanups.** `uv.lock` pins differ slightly from the old repo (`onnxruntime-gpu`); `scripts/install.py` (optional
persistent registration): fixed 2026-10-29 (`--remove` keeps `add-project` entries; default port now 8766); many scripts and
the `ur5e-dt` extension still say `UR5E_DT_*` (intentional: that is the twin pack's namespace); `docs/` still has a few
upstream-written pages (`mcp/docs/*`) that predate the rename and may mention old paths.

## 6. Other repos and machine state

- **`ur5e-digital-twin`** (Kaleb's research repo, `~/Desktop/ur5e-digital-twin`): back on `master` (two submodules, Isaac Sim 5.1,
  which **crashes on the lab box's driver 595**). Local `master` is ahead 1 / behind 1 of `origin/master` (diverged; not resolved).
  PR #1 (consolidation) was closed; branch `consolidate-62` is the archive of that work and contains the 6.2 probe, TF rewiring,
  packaging and `learning6/`. Its `CLAUDE.md` still refers to MCP server `ur5e` and `scripts/launch_ur5e_dt.sh`; update it if that
  repo should use `isaac-mcp`.
- Old `external/` tree (pre-consolidation) is at `~/archive/external-pre-consolidation-20261029` on the lab box only.
- Two stray T3 worktrees from agent runs: `~/.t3/worktrees/ur5e-digital-twin/t3-101d33d3` and `t3-53acd119` (safe to delete).
- Transient job state lives in `~/.local/share/isaac-mcp/jobs/`; screenshots/videos in `~/Pictures/screen-mcp/`, `~/Videos/screen-mcp/`.

## 7. Working preferences (from Kaleb)

- One clean name, no compatibility aliases. MIT license for everything (upstream copyright kept).
- Use OpenCode agents in parallel for bounded tasks: `opencode/beam-free` (free) by default, `runtimeMode: full-access`
  (Kaleb dislikes approval prompts); `opencode-go/deepseek-v4.1-flash` (cheap, paid) is OK for harder tasks; the free DeepSeek
  models hit a usage limit. Antigravity (`gemini-3.8-flash`) worked for drafting scripts/docs in a worktree. Always review agent
  output: the free models' code needed real fixes (path traversal, wrong Isaac Lab flags) that only a real run exposed.
- Delegation notes (observed 2026-10-29, T3 Code): a child's runtime mode cannot exceed the thread's, so set the thread to
  `full-access` before delegating at full access. `opencode/beam-free`, `opencode-go/minimax-m3.1-flash` and
  `opencode-go/deepseek-v4.1-flash` produced usable diffs; `nvidia/deepseek-ai/deepseek-v4.1-flash` and `nvidia/z-ai/glm-5.3-flash`
  work after a retry on interruption; `nvidia/minimaxai/minimax-m3.1-flash` failed with "Invalid request" on long tool-using tasks;
  `nvidia/openai/gpt-oss-20b` returned a fabricated "none found" audit (never trust it for verification). Antigravity children
  are sandboxed to the thread's own workspace (so run delegates from a thread whose project is this repo); its Claude models hit a
  usage limit while `gemini-3.8-flash` still answered. Always review the diff and re-run the tests: agents added noisy edits, a
  sleep-based workaround in place of a real fix, and one wrong default.
- Prefer targeted checks over `scripts/probe_62.sh` (slow); avoid GPU jobs while agents run.
- Do not create public repos, publish packages, or open PRs without being asked.

## 8. Quick command reference

```bash
./run.sh [--headless] [--port N]               # Kit + bridge (default 8766)
./verify.sh                                    # tool list + physics checks against a running twin
scripts/check_machine.sh                       # OS, GPU, driver, python, isaacsim, ROS libs, CUDA kernel
scripts/verify_tf.sh                           # records /tf_cam and /objects_poses_sim to /tmp/opencode/tf_after.json
scripts/catch_hang.sh [N] [timeout]            # hunt the quick_start flake
cd mcp && uv build                             # wheel + sdist
git log --oneline                              # one commit per step; history starts at the seed commit 2353c0f
```
