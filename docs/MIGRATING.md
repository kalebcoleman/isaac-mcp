# Migrating from isaac-mcp

twinsmith was called isaac-mcp until 0.3.0. Nothing about what it does changed; the names did. This page lists every old
name next to its new one, the commands that move your files, and how to update an MCP client entry. It is the only page
that names the old spellings.

Old environment variables, config file names and output markers still work for one release (0.3.x) and print one warning
per name that says what to use. They are removed in the release after that. Names of folders and processes that the host
and the simulator must agree on changed at once, with no alias; so do the tool names listed below.

## The short version

1. Stop every running simulator and MCP server.
2. Reinstall the command: `uv tool uninstall isaac-mcp`, then `./install.sh --no-sim` (or `uv tool install` from `mcp/`).
3. Move your folders and files with the commands [below](#folders-and-files).
4. Replace the client entry (`isaac` becomes `twinsmith`) and rename the variables in it.
5. Clean up the Kit registration with `python3 scripts/install.py --remove`, then register again.

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

The simulator-environment project in the repository root (`pyproject.toml`) is now `twinsmith-sim`. Isaac Sim and Isaac Lab
keep their own names everywhere: `ISAACSIM_PATH`, `ISAAC_PATH`, `ISAACLAB_*` (except the one below), `isaacsim`, `isaaclab`.
The pack id `ur5e-jenga-workcell`, its version `v1`, the `pack.yaml` fields and the keys of `lab_hardware.json` did not change.

## Tools

| Before | From 0.3.0 |
|---|---|
| `isaac_help` | `help` |
| `ur5e_dt__workcell` (actions `quick_start`, `status`, `shutdown`) | `workcell__cell` |
| `ur5e_dt__robot`, `__perception`, `__jenga`, `__real2sim`, `__planning`, `__eval` | `workcell__robot`, `workcell__perception`, `workcell__jenga`, `workcell__real2sim`, `workcell__planning`, `workcell__eval` |

The simulator-side action names (`workcell_quick_start`, `robot_status`, `jenga_build`, ...) did not change. Every other
listed tool keeps its name (`sim__*`, `assets__*`, `isaaclab__*` and the host tools). Rename the tools in your client's
permission rules too: a rule for `mcp__isaac__status` becomes `mcp__twinsmith__status`.

## Environment variables

Every `ISAAC_MCP_<X>` becomes `TWINSMITH_<X>`, with these exceptions.

| Before | From 0.3.0 | Note |
|---|---|---|
| `ISAAC_MCP_PORT`, `UR5E_DT_PORT`, `ISAAC_SIM_PORT` | `TWINSMITH_PORT` | Three names for the bridge port become one. If several old names are set, the first in this order wins, and the new name beats all of them |
| `ISAAC_MCP_PYTHON`, `UR5E_DT_PYTHON`, `ISAAC_SIM_PYTHON` | `TWINSMITH_SIM_PYTHON` | Three names for the simulator's interpreter become one |
| `UR5E_DT_HEADLESS` | `TWINSMITH_HEADLESS` | |
| `ISAACLAB_JOBS_DIR` | `TWINSMITH_JOBS_DIR` | The job folder serves simulator jobs too. The other `ISAACLAB_*` names are unchanged, `ISAACLAB_EXTERNAL_CALLBACK` included |
| `ISAAC_MCP_EXTERNAL_CALLBACK` | `TWINSMITH_EXTERNAL_CALLBACK` | The internal hand-off to an Isaac Lab job |
| `ISAAC_MCP_ASSET_ROOT` | `TWINSMITH_ASSET_ROOT` | The old name is a **permanent** alias: it keeps working, with no warning and no removal date |
| `ISAAC_SIM_PATH` | none | Removed. It was a second spelling of NVIDIA's `ISAACSIM_PATH`; use that |
| `UR5E_DT_SHOW_PANEL`, `UR5E_DT_CAMERA_CONFIG` | none | Removed. Nothing reads them |

Where the old name is read: the host server, the Kit bridge and the shell launchers (`run.sh`, `install.sh`,
`scripts/check_machine.sh`) all apply the same table, which is `mcp/twinsmith/legacy.py`. Set the new names in your shell
profile and in your client entry; the warnings tell you which old ones are left.

### Settings that were already removed

Two groups of settings stopped working before 0.3.0. They still stop the server or are announced once, and the messages
name the new variable.

| Removed | Use instead |
|---|---|
| `ISAAC_MCP_LOCAL_TOOLS`, `ISAAC_MCP_NAMESPACE`, `ISAAC_MCP_BUILTINS`, `ISAAC_MCP_SCENE_TOOLS`, `ISAAC_MCP_GROUP_TOOLS` | `TWINSMITH_TOOLSETS` (see [toolsets](TOOLSETS.md)). A server that finds one set refuses to start and prints the replacement line |
| `ISAAC_MCP_ASSET_PATH`, `UR5E_DT_ASSET_CACHE` | `TWINSMITH_ASSET_ROOT`: one folder |
| `ISAAC_MCP_ASSET_PACK` | the project file's `"pack": "<id>/<version>"`, or the tool's `pack` argument |
| `ISAAC_MCP_ASSET_STRICT`, `UR5E_DT_OFFLINE` | nothing: nothing is ever downloaded |
| `ISAAC_MCP_ASSET_REMOTE_ROOT`, `UR5E_DT_ASSET_ROOT` | nothing: put the file in a pack |

## Folders and files

Move these with nothing running. The old folders are not read any more, so a job, a session or a log left in one is not
found until you move it.

```bash
mv ~/.config/isaac-mcp          ~/.config/twinsmith           # config.toml, approval.json
mv ~/.local/share/isaac-mcp     ~/.local/share/twinsmith      # jobs, sim-sessions
mv ~/.local/state/isaac-mcp     ~/.local/state/twinsmith      # server logs
mv ~/.cache/isaac-mcp           ~/.cache/twinsmith            # import staging
mv ~/Pictures/screen-mcp        ~/Pictures/twinsmith          # screenshots
mv ~/Videos/screen-mcp          ~/Videos/twinsmith            # recordings
```

Project files, in each project that has one (the old names are still read in 0.3.x, with a warning that names the new file):

```bash
mv isaac-mcp.toml twinsmith.toml      # [tools] toolsets, [sim] physics_backend
mv isaac-mcp.json twinsmith.json      # "pack", "isaaclab_python", "isaaclab_root", ...
```

If you set `TWINSMITH_ASSET_CONFIG` to a file under the old config folder, point it at the moved file.

These runtime names changed with no alias, because the host and Kit must agree on them; nothing to move, they are recreated:
the token and portfile folder (`/tmp/isaac-mcp-<uid>`, or `isaac-mcp/` under `$XDG_RUNTIME_DIR`), the desktop-input folder
(`isaac-mcp-desktop`) and the managed scene-output folders (`isaac-mcp-scenes-<pid>`). A simulator started before the
upgrade advertises its bridge in the old place, so restart it.

The asset-root folder name `isaac-mcp-assets` is still found, with a warning that the name is transitional. Rename it to
`assets/` inside your workspace, or set `TWINSMITH_ASSET_ROOT` to it. Nothing under it needs to change.

## Output markers

A script run with `run_script` can print a line to list an output or report progress. The markers are now
`TWINSMITH_ARTIFACT: <path>` and `TWINSMITH_PROGRESS: <fraction> [text]`. The old `ISAAC_MCP_ARTIFACT:` and
`ISAAC_MCP_PROGRESS:` lines are still understood in 0.3.x.

## Updating an MCP client entry

Replace the entry, not only its name: the command and the variables changed too.

Claude Code:

```bash
claude mcp remove --scope user isaac
claude mcp add --scope user twinsmith -e TWINSMITH_PORT=8766 -- twinsmith
```

`./install.sh --claude-code` does this for you. Other clients: rename the key `isaac` to `twinsmith`, the command
`isaac-mcp` to `twinsmith` (`uvx twinsmith` if you start it with `uvx`), and the variables in its `env` per the table above.
For example:

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

## Where the old names still exist

The old Python package and extension names are gone, so imports of `isaac_mcp` and `isaac_mcp_bridge`, and `--enable
isaac.mcp.bridge` or `--enable ur5e-dt` on a Kit command line, must be changed. The upstream projects this one grew from
keep their own names in [VENDORING.md](VENDORING.md).
