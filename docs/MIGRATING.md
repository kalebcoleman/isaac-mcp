# Migrating from isaac-mcp

twinsmith was called isaac-mcp until 0.3.0. Until the old names were removed on 2026-11-13 (PR #113, merge commit
`e54ae49`), twinsmith still read the old environment variables, config file names and output markers; a checkout or
install that includes that commit no longer reads them. The asset-root alias `ISAAC_MCP_ASSET_ROOT` and the folder
names `<home>/isaac-workspace` and `<workspace>/isaac-mcp-assets` were kept a little longer, and were removed by the
pull request that followed #113. This page is for anyone coming from isaac-mcp 0.2, or from a twinsmith setup older than
those changes that still uses them. It is the only page that names the old spellings.

A host server or a Kit bridge that finds an old setting refuses to start and names it, so a leftover
`ISAAC_MCP_READ_ONLY` or `ISAAC_MCP_TOOLSETS` cannot quietly change what a client may do; the message names what replaces
each one. `run.sh` refuses too, whatever its flags, before it probes the interpreter or starts Kit. An old `config.toml`
under `~/.config/isaac-mcp` or an old `isaac-mcp.toml` is refused the same way while its new file is missing. The old
folders are not read: move them (see [Folders and files](#folders-and-files) and [Workspace folder](#workspace-folder));
an asset root that is not found names the old folder it sees.

## The short version

1. Stop every running simulator and MCP server.
2. Reinstall the command: `uv tool uninstall isaac-mcp`, then `./install.sh --no-sim` (or `uv tool install` from `mcp/`).
3. Rename the variables in your shell profile and client entries with the [table below](#environment-variables).
4. Move your folders and files with the commands [below](#folders-and-files).
5. Replace the client entry: remove `isaac` and add `twinsmith` ([how](#updating-an-mcp-client-entry)).
6. If you registered the bridge with `scripts/install.py`, clean up and register again (see [Kit](#kit-and-the-installer)).

## Names

| What | Before 0.3.0 | From 0.3.0 |
|---|---|---|
| Distribution, command, repository | `isaac-mcp` | `twinsmith` |
| Python package | `isaac_mcp` (`mcp/isaac_mcp/`) | `twinsmith` (`mcp/twinsmith/`) |
| MCP server name in a client entry | `isaac` | `twinsmith` |
| Tool prefix some clients add | `mcp__isaac__` | `mcp__twinsmith__` |
| Resource | `isaac-mcp://toolsets` | `twinsmith://toolsets` |
| Bridge extension id | `isaac.mcp.bridge` | `twinsmith.bridge` |
| Bridge module and folder | `isaac_mcp_bridge`, `exts/isaac.mcp.bridge/` | `twinsmith_bridge`, `exts/twinsmith.bridge/` |
| Kit settings subtree | `/persistent/exts/isaac.mcp.bridge` | `/persistent/exts/twinsmith.bridge` |
| Work-cell extension id and module | `ur5e-dt`, `ur5e_dt` (`exts/ur5e-dt/`) | `workcell` (`exts/workcell/`) |
| Work-cell bridge namespace | `ur5e_dt` (tools `ur5e_dt.<name>`) | `workcell` (tools `workcell.<name>`, MCP names `workcell__<name>`) |
| Work-cell catalog | `catalogs/ur5e_dt.json` | `catalogs/workcell.json` |
| Scripts | `isaac_env.py`, `isaac_assets.py`, `isaac_ros_libs.sh`, `run_ur5e_dt.py` | `env.py`, `assets.py`, `ros_libs.sh`, `run_kit.py` |
| Agent skill folder | `.agents/skills/isaac-mcp` | `.agents/skills/twinsmith` |
| Output markers of `run_script` | `ISAAC_MCP_ARTIFACT:`, `ISAAC_MCP_PROGRESS:` | `TWINSMITH_ARTIFACT:`, `TWINSMITH_PROGRESS:` |

Isaac Sim and Isaac Lab keep their own names everywhere: `ISAACSIM_PATH`, `ISAAC_PATH`, `ISAACLAB_*` (except the one
below), `isaacsim`, `isaaclab`. The pack id `ur5e-jenga-workcell`, its version `v1`, the `pack.yaml` fields and the keys
of `lab_hardware.json` did not change. Imports of `isaac_mcp` or `isaac_mcp_bridge`, and `--enable isaac.mcp.bridge` or
`--enable ur5e-dt` on a Kit command line, must be changed. The upstream projects this one grew from keep their own names
in [VENDORING.md](VENDORING.md).

## Tools

| Before | From 0.3.0 |
|---|---|
| `isaac_help` | `help` |
| `ur5e_dt__workcell` (actions `quick_start`, `status`, `shutdown`) | `workcell__cell` |
| `ur5e_dt__robot`, `__perception`, `__jenga`, `__real2sim`, `__planning`, `__eval` | `workcell__robot`, `workcell__perception`, `workcell__jenga`, `workcell__real2sim`, `workcell__planning`, `workcell__eval` |

Every other listed tool keeps its name. Rename the tools in your client's permission rules too: a rule for
`mcp__isaac__status` becomes `mcp__twinsmith__status`.

## Environment variables

Every `ISAAC_MCP_<X>` becomes `TWINSMITH_<X>`, with these exceptions.

| Before | Now | Note |
|---|---|---|
| `ISAAC_MCP_PORT`, `UR5E_DT_PORT`, `ISAAC_SIM_PORT` | `TWINSMITH_PORT` | Three names for the bridge port became one |
| `ISAAC_MCP_PYTHON`, `UR5E_DT_PYTHON`, `ISAAC_SIM_PYTHON` | `TWINSMITH_SIM_PYTHON` | Three names for the simulator's interpreter became one |
| `UR5E_DT_HEADLESS` | `TWINSMITH_HEADLESS` | |
| `ISAACLAB_JOBS_DIR` | `TWINSMITH_JOBS_DIR` | The other `ISAACLAB_*` names are unchanged, `ISAACLAB_EXTERNAL_CALLBACK` included |
| `ISAAC_MCP_ASSET_ROOT` | `TWINSMITH_ASSET_ROOT` | Was an alias until the pull request after #113; now refused like the others. Rename it |
| `ISAAC_MCP_LOCAL_TOOLS`, `ISAAC_MCP_NAMESPACE`, `ISAAC_MCP_BUILTINS`, `ISAAC_MCP_SCENE_TOOLS`, `ISAAC_MCP_GROUP_TOOLS` | `TWINSMITH_TOOLSETS` | The 0.2 selection settings; see [toolsets](TOOLSETS.md) |
| `ISAAC_MCP_ASSET_PATH`, `UR5E_DT_ASSET_CACHE` | `TWINSMITH_ASSET_ROOT` | One folder that holds `<pack id>/<version>/pack.yaml` |
| `ISAAC_MCP_ASSET_PACK` | none | The project file's `"pack": "<id>/<version>"`, or the tool's `pack` argument |
| `ISAAC_MCP_ASSET_STRICT`, `UR5E_DT_OFFLINE`, `ISAAC_MCP_ASSET_REMOTE_ROOT`, `UR5E_DT_ASSET_ROOT` | none | Nothing is ever downloaded; put the files in a pack |
| `UR5E_DT_SHOW_PANEL`, `UR5E_DT_CAMERA_CONFIG` | none | Removed |
| `ISAAC_SIM_PATH` | none | Removed and **ignored**, not refused (it is not an `ISAAC_MCP_*` name). Use NVIDIA's `ISAACSIM_PATH` |

Every other `ISAAC_MCP_*` or `UR5E_DT_*` name that is set, and `ISAAC_SIM_PORT`, `ISAAC_SIM_PYTHON` or `ISAACLAB_JOBS_DIR`,
stops the host server (and `run.sh`) and keeps the Kit bridge from listening, with a message that names it. Remove it or
rename it, in your shell profile and in every client entry's `env`.

## Folders and files

Move these with nothing running. The old folders are not read, so a job, a session or a log left in one is not found
until you move it.

```bash
mv ~/.config/isaac-mcp          ~/.config/twinsmith           # config.toml, approval.json
mv ~/.local/share/isaac-mcp     ~/.local/share/twinsmith      # jobs, sim-sessions
mv ~/.local/state/isaac-mcp     ~/.local/state/twinsmith      # server logs
mv ~/.cache/isaac-mcp           ~/.cache/twinsmith            # import staging
mv ~/Pictures/screen-mcp        ~/Pictures/twinsmith          # screenshots
mv ~/Videos/screen-mcp          ~/Videos/twinsmith            # recordings
```

Project files, in each project that has one:

```bash
mv isaac-mcp.toml twinsmith.toml      # [tools] toolsets, [sim] physics_backend (refused until moved)
mv isaac-mcp.json twinsmith.json      # "pack", "isaaclab_python", "isaaclab_root", ... (no longer found)
```

If you set `TWINSMITH_ASSET_CONFIG` to a file under the old config folder, point it at the moved file. A RoboSimStudio run
written by isaac-mcp (ledger `isaac-mcp-robosim-v1`) is no longer resumed; start a new run.

The runtime folders (the token and portfile folder `/tmp/isaac-mcp-<uid>`, `isaac-mcp-desktop`, `isaac-mcp-scenes-<pid>`)
are recreated under the new names; nothing to move. A simulator started by isaac-mcp advertises its bridge in the old
place, so restart it.

The asset-root folder name `isaac-mcp-assets` is no longer read. Rename it to `assets/` inside your workspace, or set
`TWINSMITH_ASSET_ROOT` to it. Nothing under it needs to change. An old-format pack in it can be converted with
`scripts/assets.py adopt --from-old`.

## Workspace folder

The default workspace is `<home>/robotics-workspace` (a general robotics workspace: `twinsmith/`, `assets/`, `envs/`,
`projects/`, `tools/`, `runs/`), no longer `<home>/isaac-workspace`. Nothing is moved for you, and the old folder is not
read: with only `<home>/isaac-workspace`, the asset root is not found (the error says where to move it) and
`scripts/env.py init` stops and prints the `mv`. `TWINSMITH_WORKSPACE` always wins. To move, only when no simulator or job
is running from the workspace:

```bash
mv ~/isaac-workspace ~/robotics-workspace && ln -s ~/robotics-workspace ~/isaac-workspace
mv ~/robotics-workspace/isaac-mcp-assets ~/robotics-workspace/assets
```

The symlink keeps old paths (shell profiles, saved scenes, running jobs) working; remove it when nothing uses it. Then
check with `python3 scripts/env.py where` and `assets__pack action=list`.

## Updating an MCP client entry

Replace the entry, not only its name: the command and the variables changed too. `./install.sh --claude-code` adds the
`twinsmith` entry but does **not** remove an old `isaac` entry, so remove that one yourself.

Claude Code:

```bash
claude mcp remove --scope user isaac
claude mcp add --scope user twinsmith -e TWINSMITH_PORT=8766 -- twinsmith
```

Other clients: rename the key `isaac` to `twinsmith`, the command `isaac-mcp` to `twinsmith` (`uvx twinsmith` if you start
it with `uvx`), and the variables in its `env` per the table above. For example:

```json
{"mcpServers": {"twinsmith": {"command": "twinsmith", "env": {"TWINSMITH_PORT": "8766"}}}}
```

Codex and OpenCode take the same change: server `twinsmith`, command `twinsmith`, `TWINSMITH_*` variables. Reconnect the
client, then call `status` once.

## Kit and the installer

Kit stores the extension ids and the bridge settings in a `user.config.json`. With Kit closed, run

```bash
python3 scripts/install.py --remove      # removes the old ids (isaac.mcp.bridge and its stored settings) and the new ones
python3 scripts/install.py               # registers twinsmith.bridge
```

If you start Kit with `run.sh`, nothing is stored and there is nothing to clean up: `run.sh` enables `twinsmith.bridge`,
`workcell` and `sim-tools` itself.
