# One workspace folder

Keep the isaac-mcp checkout, your asset stores and your Isaac Sim / Isaac Lab Python
environments together in one folder. This page says how, and how the tool finds things
that are already installed somewhere else.

**Platform: Linux x86_64 only** (Ubuntu 24.04 or 22.04; see [Linux setup](LINUX-SETUP.md)).
This page documents no Windows or macOS paths. A Windows install, for example one under
`C:` and reached from WSL as `/mnt/c/...`, cannot be used. That is not supported and
untested.

## Why one folder

- **Reproducible.** One root holds everything the setup needs. You can write down what is
  in it and rebuild it.
- **Projects never edit the MCP checkout.** The checkout is code only. Your scenes, notes and
  asset changes live in `projects/` and `isaac-mcp-assets/`, so updating or re-cloning the checkout
  cannot lose them.
- **Easy to copy.** Moving to a second machine means copying `isaac-mcp-assets/` and checking hashes
  (see [Move to a second machine](#move-to-a-second-machine)).

## Layout

```text
<workspace>/
  isaac-mcp/     the checkout (code only, never edited by projects)
  isaac-mcp-assets/  your local asset database (isaac-mcp-assets/ur5e-dt/..., SHA256SUMS); never pushed
  envs/          Isaac Sim / Isaac Lab Python environments (e.g. sim-6.2/, lab-3.0/)
  projects/      your projects, each may hold an isaac-mcp.json
  runs/          logs, job outputs, captures
```

`<workspace>` can be any folder you own. The default is `~/isaac-workspace`, or whatever
`ISAAC_MCP_WORKSPACE` says. It must not be inside the checkout; `init` refuses that.

## Agent rules for every project

`init` writes `<workspace>/AGENTS.md` and `<workspace>/CLAUDE.md` (which only contains `@AGENTS.md`). Claude Code, Codex
and OpenCode read parent-folder instruction files, so every project under `projects/` gets them. The text is the `AGENTS`
constant in `scripts/isaac_env.py`. In short: load the `mcp__isaac` tools, run `status` and `list_bridges`, use the
screenshot, recording, contact and Isaac Lab tools, and say why before falling back to plain scripts. A project's own
`AGENTS.md` can add to it. Run `init` again on an old workspace to add the files; nothing is overwritten.

To see at the start of every session whether a bridge is up, add a Claude Code hook to `~/.claude/settings.json`.
`scripts/session_check.py` prints one line and always exits 0:

```json
{"hooks": {"SessionStart": [{"hooks": [{"type": "command",
  "command": "python3 /path/to/isaac-mcp/scripts/session_check.py"}]}]}}
```

## Set up a new machine

1. **Create the folder and clone into it.**

   ```bash
   export ISAAC_MCP_WORKSPACE=~/isaac-workspace      # choose your own location
   mkdir -p "$ISAAC_MCP_WORKSPACE"
   git clone <repo-url> "$ISAAC_MCP_WORKSPACE/isaac-mcp"
   cd "$ISAAC_MCP_WORKSPACE/isaac-mcp"
   ```

2. **Create the sub-folders.** `init` never overwrites anything that exists. New folders are
   not group- or world-writable (0755 under the usual umask).

   ```bash
   python3 scripts/isaac_env.py init
   ```

   It creates `isaac-mcp-assets`, `envs`, `projects`, `runs` and a short README under
   `$ISAAC_MCP_WORKSPACE`. Use `--workspace DIR` to name the folder explicitly.

3. **Make the choice permanent.** Add the export to your shell profile (for example
   `~/.bashrc`) and to the environment of your MCP client config
   ([client configuration](MCP-SERVER.md#4-client-configuration)).

   ```bash
   echo 'export ISAAC_MCP_WORKSPACE=~/isaac-workspace' >> ~/.bashrc
   ```

4. **Install or point to Isaac Sim.** For a new pinned 6.2 install, follow
   [Linux setup](LINUX-SETUP.md#new-pinned-62-installation); the installer creates
   `<repo>/.venv`, which is found automatically. If you already have Isaac Sim, see
   [I already have Isaac Sim installed](#i-already-have-isaac-sim-installed).

5. **Add your assets and check.** `<workspace>/isaac-mcp-assets` is the asset folder when
   `ISAAC_MCP_WORKSPACE` is set (otherwise `isaac-mcp-assets` next to the checkout, which is
   the same folder in this layout). Fill it with `scripts/isaac_assets.py add`, `adopt` or
   `migrate` and `scripts/fetch_ur5e_assets.py` (see [ASSETS.md](ASSETS.md)).

   ```bash
   python3 scripts/isaac_assets.py add /path/to/lab-assets.tar.gz --store "$ISAAC_MCP_WORKSPACE/isaac-mcp-assets"
   python3 scripts/isaac_assets.py doctor
   python3 scripts/isaac_env.py where
   ./run.sh --port 8766   # not if a Kit already holds this port; choose another with --port
   ```

## I already have Isaac Sim installed

Do not reinstall. Ask the tool what it can find:

```bash
python3 scripts/isaac_env.py find          # human-readable list
python3 scripts/isaac_env.py find --json   # same, for scripts
```

`find` lists the Isaac Sim and Isaac Lab environments it discovers, in the search order
below. It only looks at files. It does **not** run any interpreter, so a listed entry is
a candidate, not proof that the environment works.

Pick one:

```bash
python3 scripts/isaac_env.py resolve-python                       # print the chosen interpreter
python3 scripts/isaac_env.py resolve-python --require-version 6.2 # only a matching version
python3 scripts/isaac_env.py resolve-python --lab                 # prefer an environment that also has Isaac Lab
```

Then confirm it before you rely on it:

```bash
PY="$(python3 scripts/isaac_env.py resolve-python)" &&
  "$PY" -c 'import importlib.metadata as m, sys; print(sys.version); print(m.version("isaacsim"))' &&
  uv pip check --python "$PY"
```

**Pin it.** Auto-detect is a convenience. To make the choice explicit, set
`ISAAC_MCP_PYTHON`. It wins over everything else:

```bash
export ISAAC_MCP_PYTHON=<env>/bin/python
./run.sh --port 8766
```

**Link it into the workspace.** If you want the environment to be listed with the rest,
symlink it. Nothing is moved or modified:

```bash
ln -s <env> "$ISAAC_MCP_WORKSPACE/envs/sim-6.2"
```

`<env>` is the directory that holds `bin/python`. Anything in `<workspace>/envs/*/bin/python`
is found.

`run.sh` follows the same rules. It prints which interpreter it chose and how to pin it.

## What is auto-detected, and where it looks

The first match in this order is used, even if a newer Isaac Sim exists further down the
list; the version only filters. `run.sh` asks for any Isaac Sim 6.x
(`resolve-python --require-version 6`). Pin a specific interpreter with `ISAAC_MCP_PYTHON`.

1. `ISAAC_MCP_PYTHON`, then `UR5E_DT_PYTHON` (deprecated alias), then `ISAAC_SIM_PYTHON`.
2. `<workspace>/envs/*/bin/python`.
3. `<repo>/.venv/bin/python`.
4. The active environment: `$VIRTUAL_ENV` or `$CONDA_PREFIX`.
5. Sibling environments next to the checkout.
6. Conda-style roots and common virtual-environment folders under your home directory, then
   project virtual environments one and two folders below it (`~/<project>/.venv`,
   `~/<group>/<project>/.venv`; hidden folders are skipped, nothing deeper is scanned).
7. Standalone installs: `$ISAACSIM_PATH`, `$ISAAC_SIM_PATH`, `$ISAAC_PATH`, `~/isaacsim`,
   `~/isaac-sim`, `~/.local/share/ov/pkg/isaac-sim-*`, `/opt/nvidia/isaac-sim*`,
   `/isaac-sim`.

The asset stores are searched in this order (see [ASSETS.md](ASSETS.md#lookup-order)):
project stores, then either `ISAAC_MCP_ASSET_PATH` (when set, it **replaces** the defaults) or
the asset folder (`<workspace>/isaac-mcp-assets`, or `isaac-mcp-assets` next to the checkout)
followed by older locations that still exist (`<workspace>/assets`,
`~/.cache/ur5e-dt/assets`), read only. `isaac_assets.py migrate` moves them.

## What is not auto-detected

- Any Windows install, including under `C:` or `/mnt/c`. Not supported, untested.
- macOS. Not supported.
- Environments outside the places listed above. Set `ISAAC_MCP_PYTHON`, or symlink the
  environment into `<workspace>/envs/`.
- A version that matches. `find` does not execute Python, so check the version yourself
  as shown above, or use `--require-version`.
- System ROS. The launcher sources `/opt/ros/<distro>/setup.bash` (default; `ROS_SETUP` and
  `ROS_DISTRO` override it). See [Linux setup](LINUX-SETUP.md#optional-ur5e-mock-ros-driver).
- Isaac Lab for the `isaaclab__*` job tools is selected with `ISAACLAB_PYTHON` and
  `ISAACLAB_ROOT`, see [TOOLS-ISAACLAB.md](TOOLS-ISAACLAB.md).

## Environment variables

| Variable | What it does | Default |
|---|---|---|
| `ISAAC_MCP_WORKSPACE` | The workspace folder for `isaac_env.py` and the asset-store lookup. | `~/isaac-workspace` for `isaac_env.py`. The asset folder becomes `<workspace>/isaac-mcp-assets`. |
| `ISAAC_MCP_PYTHON` | Simulator interpreter for `run.sh`. Highest priority. | unset (auto-detect) |
| `UR5E_DT_PYTHON` | Deprecated alias of `ISAAC_MCP_PYTHON`. | unset |
| `ISAAC_SIM_PYTHON` | Lowest-priority explicit interpreter. Honoured by `scripts/isaac_env.py` auto-detection only: `run.sh` itself checks `ISAAC_MCP_PYTHON`, `UR5E_DT_PYTHON` and `<repo>/.venv` first, so an existing `.venv` wins over it. | unset |
| `ISAACSIM_PATH`, `ISAAC_SIM_PATH`, `ISAAC_PATH` | A standalone Isaac Sim install folder to search. | unset |
| `ISAAC_MCP_ASSET_PATH` | Asset stores, separated by `:`. Alias `UR5E_DT_ASSET_CACHE`. | `isaac-mcp-assets` next to the checkout (or `<workspace>/isaac-mcp-assets`) |
| `ISAAC_MCP_PROJECT` | A project directory, or its `isaac-mcp.json`. | walk up from the working directory |
| `ISAAC_MCP_SIM_ROOT` | Checkout (with `run.sh`) that managed `sim__launch` sessions use. Set it when the host is installed outside a checkout. | the repository root when available |

Set the variables in the shell that starts `run.sh` and in your MCP client's environment.
Setting one only in the client does not change an already-running Kit process.

## Move to a second machine

Paths differ between machines. The bytes must not.

1. On the second machine, do [the setup above](#set-up-a-new-machine) steps 1 to 4.
2. Copy the asset folder `isaac-mcp-assets/` (it includes the `SHA256SUMS` pin files) to the same place.
   Keep each pack folder whole.
3. Run on both machines and compare:

   ```bash
   python3 scripts/isaac_assets.py doctor --json > doctor-$(hostname).json
   ```

   For each `relpath`, `status` and `sha256` must match. Ignore `path`. See
   [ASSETS.md](ASSETS.md#verify-on-two-machines).
4. Environments are not copied. Install the same Sim version on the second machine and check
   it with the version one-liner above.

## Troubleshooting

**`find` lists nothing.** Your install is outside the search locations. Set
`ISAAC_MCP_PYTHON`, or symlink it into `<workspace>/envs/`.

**`init` refuses the folder.** It is inside the checkout. Choose a folder outside it.

**`run.sh` picked the wrong interpreter.** Read its printed line, then pin the one you want
with `ISAAC_MCP_PYTHON`. `python3 scripts/isaac_env.py where` shows the effective workspace
and interpreter.

**Assets are not found.** The asset folder must be the one the Kit process sees: check
`ISAAC_MCP_ASSET_PATH` / `ISAAC_MCP_WORKSPACE` in the Kit process's environment. Run
`python3 scripts/isaac_assets.py where` to see the stores in order.

**Found but broken.** An interpreter listed by `find` can still be incompatible. Run the
version one-liner and `uv pip check` shown above.
