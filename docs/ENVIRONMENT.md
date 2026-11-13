# Environment variables

Every setting, grouped by the process that reads it. **This matters:** a variable set only in your MCP
client's entry never reaches a Kit process that is already running. Set Kit variables in the shell that
starts `run.sh`. Set host variables in the client's `env` for the `isaac` server, then reconnect.

## Host server (`isaac-mcp`, your MCP client's entry)

| Variable | Default | Meaning |
|---|---|---|
| `ISAAC_MCP_PORT` | unset | Bridge port to pin; the installer writes this one. With no port set, a lone advertised bridge is discovered; several make discovery refuse. |
| `UR5E_DT_PORT` | unset | Older name for the same pin, which older client entries still use. Takes precedence over `ISAAC_MCP_PORT`. |
| `ISAAC_MCP_HOST` | discovered bridge | Bridge host. |
| `ISAAC_MCP_TOOLSETS` | `default` | Toolset profile and adjustments. Overrides user and project TOML. See [toolsets](TOOLS-HOST.md#choosing-which-local-tools-a-server-exposes). |
| `ISAAC_MCP_PHYSICS_BACKEND` | `physx` | Configured backend (`physx` or `newton`); selection is recorded only, not applied to a simulator yet. |
| `ISAAC_MCP_NAME`, `ISAAC_MCP_INSTRUCTIONS` | built in | Cosmetic server identity. |
| `ISAAC_MCP_LOG` | `1` | `0` turns off the host server's durable log file (stderr logging stays). See [Diagnosing disconnects](MCP-SERVER.md#diagnosing-disconnects-the-server-log). |
| `ISAAC_MCP_LOG_DIR` | `$XDG_STATE_HOME/isaac-mcp`, else `~/.local/state/isaac-mcp` | Directory of the host's `server-<pid>.log` (1 MB x 3, private). The bridge reads the same variable for its failure log. |
| `DISPLAY` | `:0` | X11 display for the desktop tools. Match your session. |

The filters are visibility preferences, not a security boundary; the token and read-only mode are enforced by the bridge. See
[limiting what a client can reach](MCP-SERVER.md#limiting-what-a-client-can-reach).

### Managed sessions (`sim__launch`, opt-in)

| Variable | Default | Meaning |
|---|---|---|
| `ISAAC_MCP_PYTHON` | auto-detect | Simulator interpreter passed to `run.sh`. The installer writes it into the Claude entries it creates. |
| `ISAAC_MCP_SIM_ROOT` | repository root when available | Checkout containing `run.sh`. Set it when the host is installed outside a checkout. |
| `ISAAC_MCP_SIM_SESSIONS_DIR` | `~/.local/share/isaac-mcp/sim-sessions` | Session metadata, logs and output. |
| `ISAAC_MCP_PORT_RANGE` | unset | Ports `sim__launch` may use, as `18800-18899`. An omitted port is chosen inside it; a stated port outside it is refused. |
| `ISAAC_MCP_ROS_DOMAIN_RANGE` | unset | ROS domains `sim__launch` may use, as `40-49`. An omitted domain is chosen inside it; a stated domain outside it is refused. |
| `ISAAC_MCP_SIM_MAX_INSTANCES` | `2` | Maximum known bridge, managed Sim and Lab-job processes. `0` disables the limit. |
| `ISAAC_MCP_SIM_MIN_FREE_VRAM_MIB` | `8192` | Minimum free GPU memory at launch. |
| `ISAAC_MCP_SIM_MIN_FREE_RAM_MIB` | `4096` | Minimum available system RAM at launch. |

### Isaac Lab tools

| Variable | Default | Meaning |
|---|---|---|
| `ISAACLAB_PYTHON` | discovered | Lab interpreter. An invalid explicit path fails discovery instead of falling back. It takes priority over `isaaclab_python` in a named project's `isaac-mcp.json` ([project setting](TOOLS-ISAACLAB.md#setup-and-configuration)). |
| `ISAAC_MCP_SIM_LAUNCHER` | unset | With the `launch` toolset: [a project's own launcher](TOOLS-HOST.md#a-projects-own-launcher) for `sim__launch`. Only from here, never from a tool argument or project file. |
| `ISAACLAB_ROOT` | discovered | Lab checkout. |
| `ISAACLAB_JOBS_DIR` | `~/.local/share/isaac-mcp/jobs` | Job records, for Isaac Lab jobs and simulator jobs alike. |
| `ISAACLAB_LOGS_DIR` | unset | Extra run directory for checkpoints and metrics. |
| `ISAACLAB_GPU_BUDGET_HOURS` | unset | Optional GPU-hour cap for Lab jobs (off by default; see [preflight](TOOLS-ISAACLAB.md#optional-gpu-hours-used)). |
| `ISAACLAB_GPU_LEDGER` | unset | Optional JSON file for that cap. |
| `ISAACLAB_TASK_MODULES` | unset | Modules to import so custom tasks register. |
| `ISAACLAB_TASK_PATH` | unset | Added to `PYTHONPATH` for those modules. |
| `ISAACLAB_EXTERNAL_CALLBACK` | unset | Default `module.function` for Lab jobs: `--external_callback=` for rsl_rl train/play, called by the server for other libraries, `env_check` and `record`. |
| `ISAACLAB_INSTALLS` | unset | `os.pathsep` list of extra Isaac Lab venvs (or folders of them) that the `install` argument can select by name. |
| `ISAACLAB_CHECKPOINT_ROOTS` | unset | `os.pathsep` list of extra folders train, eval and play may load checkpoints from. |
| `ISAACLAB_ALLOW_TMP` | unset | `1` allows the temp directory for metrics. Tests only. |

Details are in the [Isaac Lab reference](TOOLS-ISAACLAB.md).

## Simulator (`run.sh` and the Kit process)

| Variable | Default | Meaning |
|---|---|---|
| `ISAAC_MCP_PYTHON` | see below | Simulator interpreter. `UR5E_DT_PYTHON` is a deprecated alias. |
| `UR5E_DT_PORT` | `8766` | Bridge port for `run.sh`. `ISAAC_SIM_PORT` is an older alias; `--port` overrides both. |
| `ISAAC_MCP_PHYSICS_BACKEND` | `physx` | Engine for a new session, same as `--physics-backend`. `newton` is experimental. |
| `ISAAC_MCP_NO_HOT_RELOAD` | unset | `1` stops Kit reloading extensions when their files change, same as `--no-hot-reload`. `run.sh` passes `--/app/extensions/fsWatcherEnabled=0`. Use it for recorded runs, or when another process edits this checkout. `status` reports `extension.hot_reload`. See [hot reload](TOOLS-SCENE.md#extension-hot-reload). |
| `UR5E_DT_HEADLESS` | `0` | Set by `run.sh --headless`. |
| `OMNI_KIT_ACCEPT_EULA` | unset | `YES` accepts NVIDIA's EULA, same as the installer's `--accept-eula`. |
| `ISAAC_MCP_CRASH_UPLOAD` | unset | `1` keeps Kit's default of uploading a crash's minidump to NVIDIA. Otherwise `run.sh` and Isaac Lab jobs keep dumps on this machine, in Kit's data folder, because a minidump can contain process memory. |
| `ISAAC_MCP_PRINT_PYTHON` | unset | `1` makes `run.sh` print its chosen interpreter and exit. |
| `ISAAC_MCP_SCENE_OUTPUT` | a per-process temp directory | Output root for `sim__stage__save`, `sim__camera__capture`, `sim__map__occupancy` and, in a `videos` folder, `ur5e_dt__start_recording`. **Set it to keep results across restarts.** |
| `ISAAC_MCP_ENABLE_SCENE_SCRIPTS` | unset | `1` enables `sim__controller__*` and a Python (`module:function`) controller in `sim__simulation__run`, which run arbitrary Python in Kit. A `tcp://127.0.0.1:<port>` controller needs no setting. |
| `UR5E_DT_CAMERA_CONFIG` | unset | YAML file overriding the twin's camera settings. |
| `ROS_DOMAIN_ID` | `0` | ROS domain. External ROS clients must match. |
| `ROS_SETUP`, `ROS_DISTRO` | `/opt/ros/<distro>` | Fake-driver launcher. `ROS_SETUP` names any setup file (another install, a RoboStack prefix); with it, the distribution comes from what that file sets, not from the OS. Without it, and with no `/opt/ros/<distro>`, an environment already activated (`ROS_DISTRO` set, `ros2` on `PATH`) is used as it is. |

`run.sh` chooses its interpreter from `ISAAC_MCP_PYTHON`, then `UR5E_DT_PYTHON`, then `<repo>/.venv/bin/python`, then
[auto-detection](WORKSPACE.md#what-is-auto-detected-and-where-it-looks).

### Bridge (`isaac.mcp.bridge`)

Read by Kit. Each is also a stored Kit setting under `/persistent/exts/isaac.mcp.bridge/` (`autostartPort`,
`autostartHost`, `toolModules`, `toolPaths`, `auth`, `token`, `readOnly`); `scripts/install.py` writes the first
four, and the environment wins. The [protocol](../mcp/docs/protocol.md) page explains the token, read-only mode and remote access.

| Variable | Default | Meaning |
|---|---|---|
| `ISAAC_MCP_PORT` | persisted setting | Bridge port. A taken port refuses at startup. |
| `ISAAC_MCP_BIND` | `localhost` | Bind address. Beyond loopback needs a real trust boundary, because the bridge serves `run_python`. |
| `ISAAC_MCP_TOOL_MODULES`, `ISAAC_MCP_TOOL_PATHS` | persisted setting | Your own [tool packages](../mcp/docs/tool-authoring.md) and the folders they are in. |
| `ISAAC_MCP_AUTH` | `1` | `0` starts a bridge that accepts connections without a token. |
| `ISAAC_MCP_TOKEN` | a random one per launch | The token connections must present. Set it for Kit to choose it; set the same value for a client that cannot read the bridge's token file (another machine or account). See [protocol](../mcp/docs/protocol.md#the-token). |
| `ISAAC_MCP_READ_ONLY` | `0` | `1` starts a bridge that runs only read-only tools. See [limiting what a client can reach](MCP-SERVER.md#limiting-what-a-client-can-reach). |

## Both host and Kit

These must match in both processes:

| Variable | Default | Meaning |
|---|---|---|
| `ISAAC_MCP_ASSET_CONFIG` | unset | The [trusted-project approval file](TOOLS-SCENE.md#one-time-approval). No file means no approved roots. |
| `ISAAC_MCP_STAGING` | `~/.cache/isaac-mcp/staging` | With no approval file: the private folder the server copies import snapshots into and Kit reads them from. The server and Kit must be given the same value. |
| `ISAAC_MCP_ASSET_PATH` | `isaac-mcp-assets` next to the checkout | Asset stores separated by `:`; the first receives writes. `UR5E_DT_ASSET_CACHE` is a deprecated alias. |
| `ISAAC_MCP_ASSET_PACK` | `ur5e-dt` | Selects the [asset pack](ASSETS.md#named-packs); beats `"pack"` in `isaac-mcp.json`. An invalid name is an error. |
| `ISAAC_MCP_USD_PYTHON` | unset | A Python with `usd-core` for USD dependency checks on the MCP host; without it they use the selected Kit. |
| `ISAAC_MCP_ASSET_STRICT` | unset | `1` disables the twin's remote asset fallback. `UR5E_DT_OFFLINE` is a deprecated alias. |
| `ISAAC_MCP_ASSET_REMOTE_ROOT` | NVIDIA's asset root | Fetch source. `UR5E_DT_ASSET_ROOT` is a deprecated alias. |
| `ISAAC_MCP_WORKSPACE` | unset | [Workspace folder](WORKSPACE.md). `isaac_env.py` falls back to `~/isaac-workspace`. |
| `ISAAC_MCP_PROJECT` | walk up from the working directory | A project directory or its `isaac-mcp.json`. Only a project named here can set `isaaclab_python` and `isaaclab_root`; a file found by the walk is ignored for those two keys. |

## Install discovery

`scripts/isaac_env.py` also reads `ISAAC_SIM_PYTHON` (lowest-priority interpreter) and `ISAACSIM_PATH`, `ISAAC_SIM_PATH` and
`ISAAC_PATH` (standalone install folders). See [what is auto-detected](WORKSPACE.md#what-is-auto-detected-and-where-it-looks).

## Other

`ISAAC_MCP_EXTS` locates the extensions for the [Isaac Lab bridge template](../mcp/templates/isaaclab_bridge_launch.py).

Internal handshakes, not meant to be set by hand: `ISAAC_MCP_DEFER_START`, `ISAAC_MCP_SIM_SESSION_DIR`,
`ISAAC_MCP_SIM_SESSION_ID`, `ISAAC_MCP_TF_DOCTOR_REEXEC`.
