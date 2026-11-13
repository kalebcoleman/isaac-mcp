# Linux setup and dependencies

This guide covers the repository's Linux launcher, host MCP server, optional Isaac Lab tools, and optional ROS 2
UR5e mock driver. Install the parts needed for your workflow; the host server, simulator and external ROS nodes run
in separate environments.

**Keep it organized.** Put the checkout, the asset root and Sim environments in one workspace
folder and let the tool find an existing install. See [WORKSPACE.md](WORKSPACE.md).

## Platforms and prerequisites

This guide is **Linux x86_64 only**; Windows and macOS are not supported (a Windows `C:` install
cannot be used). The full simulator installer targets **Ubuntu 24.04 or 22.04 on x86_64**. It selects ROS 2 Jazzy on 24.04 and
Humble on 22.04; NVIDIA recommends 24.04/Jazzy and marks 22.04 support as deprecated. Check NVIDIA's current
[Isaac Sim 6.2 requirements](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/requirements.html)
for GPU, driver, RAM and storage guidance; this repository's installer requires an NVIDIA GPU, a working driver,
and at least 40 GB of free space. The installer also needs `uv`, internet access, and acceptance of the NVIDIA
Omniverse EULA. `scripts/check_machine.sh` additionally checks `git`, `curl`, networking and the display.

The recorded test machine on 2026-10-31 was Ubuntu **24.04.5 LTS (Noble)**, x86_64, NVIDIA GeForce RTX 5080 with
driver 580.178.04, Python 3.12.3, Isaac Sim 6.2.0.0, Isaac Lab 3.0.0, and ROS 2 Jazzy. That is one tested
configuration, not a claim that every Ubuntu, driver, simulator or hardware combination has been tested. NVIDIA's
published tested driver can differ; consult the requirements link above.
See [compatibility](COMPATIBILITY.md) for tested simulator releases and their individual limitations.

## Which environment does what

| Environment | Required for | Selection |
|---|---|---|
| Host Python **3.10+** | MCP connection, tool discovery, desktop capture, Lab job management | `isaac-mcp` installed by `uv tool`; independent of Sim |
| Sim Python **3.12** | Kit, rendering, physics, bridge and scene/UR tools | Explicit `ISAAC_MCP_PYTHON`, else checkout `.venv`, else auto-detected (see [WORKSPACE.md](WORKSPACE.md)) |
| Optional Isaac Lab | Training, evaluation and play | Existing `ISAACLAB_PYTHON` and `ISAACLAB_ROOT`; select a compatible Sim/Lab version pairing |
| Optional system ROS | External ROS nodes and the UR mock driver | Matching system ROS distribution, separate from Sim's bundled bridge |

No personal environment name or directory layout is required. Keep existing installations where they are;
`<workspace>` below means your workspace folder ([WORKSPACE.md](WORKSPACE.md)).
A callable offline catalog proxy still needs its runtime prerequisites to execute.

## New pinned 6.2 installation

Use a **new dedicated checkout** (recommended location: `<workspace>/isaac-mcp`, see
[WORKSPACE.md](WORKSPACE.md)) and run every command below from its root. Install
[uv](https://docs.astral.sh/uv/getting-started/installation/) first, and ensure Git and your NVIDIA
driver are available. Read NVIDIA's [requirements](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/requirements.html)
and EULA before accepting it. The repository installer checks Ubuntu, architecture, GPU,
driver and free space; its 40 GB floor is not a promise that every asset/cache workload fits.

```bash
mkdir -p <workspace>
git clone https://github.com/kalebcoleman/isaac-mcp.git <workspace>/isaac-mcp
cd <workspace>/isaac-mcp
uv --version
nvidia-smi
./install.sh --accept-eula --download-sim   # this section is the pinned 6.2 download; without --download-sim an installed Isaac Sim 6.x is used instead
export PATH="$HOME/.local/bin:$PATH"
uv pip check --python .venv/bin/python
.venv/bin/python -c 'import importlib.metadata as m, sys; print(sys.version); print(m.version("isaacsim"))'
```

`main` is the development branch, not a released stable tag. Record its exact
`git rev-parse HEAD` for reproducibility. Expect Sim **6.2.0.0** and Python **3.12**.
Stop and inspect any dependency-check failure before treating the environment as ready.

The convenience installer:

- When it finds an installed Isaac Sim 6.x and the checkout has no `.venv`, it installs only the MCP server and writes that
  interpreter into the Claude entries it creates. If nothing is found, it continues to the checks below and **stops at the
  EULA check unless `--accept-eula` was given or `OMNI_KIT_ACCEPT_EULA=YES` is already exported; in those two cases it
  downloads the environment**. An existing checkout `.venv` is re-synced unless you pass `--no-sim`, the one form that never
  downloads or syncs a simulator. `--no-sim` still looks for an installed Isaac Sim 6.x and records it in client entries.
- Runs **`uv sync --python 3.12` in the current directory** only when it is installing the simulator.
  That sync may update the root lock's environment; it does not enforce `--locked` and does not use
  `ISAAC_MCP_PYTHON` (or its alias `UR5E_DT_PYTHON`) as its install target.
- Installs the host from `mcp/uv.lock`, not from a fresh resolve:
  `uv sync --project mcp --locked --extra isaaclab --no-dev`, with `UV_PROJECT_ENVIRONMENT` set to
  `mcp/.venv` so an activated simulator environment is left alone. `uv tool install` does not read
  `uv.lock`. The installer then links `~/.local/bin/isaac-mcp` at that executable. Keep the checkout.
- Creates or refreshes its `~/.local/bin/ffmpeg` shim, preserving unrelated files/symlinks.
- Changes client configuration **only** when requested with registration flags.
  `--claude-code` replaces the user-scope `isaac` entry and removes the legacy `twin` entry;
  `--claude-desktop` updates an existing Desktop config with a backup. `--claude` requests both.
  `--print-opencode` prints configuration without writing it. Missing clients/configs can be skipped;
  inspect installer output and use the [manual client guide](MCP-SERVER.md#4-client-configuration).
- Does not install system ROS, Isaac Lab or private workstation assets.

To record an installation, see [recording a run](VERIFICATION.md#recording-a-run-so-it-can-be-reproduced).
The full installer has not been qualified from a clean machine. Its commands and parser have
been inspected; the recorded fresh-venv experiment used a separate locked sync, not `install.sh`.

### Start the simulator

From the same checkout root, after accepting the EULA:

```bash
./run.sh --port 8766
```

`run.sh` chooses the Sim interpreter in this order: `ISAAC_MCP_PYTHON` (alias `UR5E_DT_PYTHON`),
then `<repo>/.venv/bin/python`, then auto-detection (`scripts/isaac_env.py resolve-python`). It prints
the interpreter it chose and how to pin it. See [WORKSPACE.md](WORKSPACE.md#what-is-auto-detected-and-where-it-looks).

Use an unused port. A live bridge may already exist: inspect it before starting another process.
The launcher enables the bridge, generic scene tools, UR5e tools and NVIDIA's bundled ROS bridge;
Its default backend is **PhysX**; the workcell extension has no panel.
The optional Newton flag is not a compatibility promise; see the [matrix](COMPATIBILITY.md).

For a windowless GPU session, use `./run.sh --headless --port 8766` instead. To leave either running after your shell or a tool's time limit ends, use `setsid nohup ./run.sh --port 8766 > kit.log 2>&1 < /dev/null &`. Headless Sim still
requires the NVIDIA runtime and can render sensors, but has no application window for desktop
capture/input. Windowed tools were exercised on X11/XWayland; native Wayland is not qualified.
Keep the launch terminal available for startup errors. First startup can take minutes for shader
and extension preparation; wait for bridge readiness and `app_ready=true`, not a fixed sleep.
Record your setup and result as described in [recording a run](VERIFICATION.md#recording-a-run-so-it-can-be-reproduced).

`scripts/check_machine.sh` is an optional **6.2-specific** preflight. If a Sim interpreter exists,
it imports the runtime and may execute a Torch CUDA kernel. It is not a CPU-only check and should
not be run against an older build or alongside someone else's GPU job as a generic diagnostic.

## Connect and check without changing the scene

Leave the simulator running; use a second terminal from the same checkout root for terminal checks or registration.

Configure the host as a **stdio MCP server** using the [client guide](MCP-SERVER.md#4-client-configuration),
then reconnect the client. The server command is `isaac-mcp`, not the simulator Python.
`./install.sh --no-sim --claude-code --port 8766` is the explicit Claude Code registration shortcut;
it also replaces the host tool and handles the shim as described above.

In the MCP client, call `refresh_tools`, select the intended endpoint with `use_bridge` if needed,
then `status` and `sim__stage__info` with empty arguments. Verify PID, port, build, stage identity
and readiness before any editing, reset or playback. If the client does not honor tool-list change
notifications, reconnect it. No bridge, missing extension or missing Lab installation is a failure
or unavailable capability, not a successful simulation operation.

For the equivalent read-only terminal check, use the repository's standard-library client:

```bash
# From checkout root; no Sim imports and no host-package installation needed for this CLI.
python3 mcp/isaac_mcp/client.py --list-bridges
# Set these to the intended endpoint from that listing, not an arbitrary visible window.
BRIDGE_HOST=localhost
BRIDGE_PORT=8766
python3 mcp/isaac_mcp/client.py status --host "$BRIDGE_HOST" --port "$BRIDGE_PORT"
python3 mcp/isaac_mcp/client.py sim.stage.info '{}' --host "$BRIDGE_HOST" --port "$BRIDGE_PORT"
```

The direct bridge CLI uses **`sim.stage.info`**; the MCP name is **`sim__stage__info`**.
`--list-bridges` returns `[]` and exit 2 when no bridge is advertised. With several bridges,
automatic selection refuses; explicitly choose the host/port you intend. These commands do not
build the demo or start physics. Once identity is confirmed, use the [agent workflow](../AGENTS.md)
and [scene reference](TOOLS-SCENE.md) for your actual task.

## Choose a simulator build

**Existing Sim (lab and shared PCs): use it, never download another one.** `./install.sh` detects
an installed Isaac Sim 6.x and then installs only the MCP server (`--no-sim` does the same
explicitly); it downloads the locked 6.2 environment only when nothing is found or with
`--download-sim`. Install only the host, inspect your interpreter, then select it explicitly if needed.
List what the tool can find with `python3 scripts/isaac_env.py find`. Replace `<env>` with your own
compatible installation. Do not rename, relocate or reinstall an environment just to match this guide
(a symlink into `<workspace>/envs/` is optional and changes nothing).

```bash
# From checkout root; this replaces only the user-level host tool and may create its ffmpeg shim.
# Export ISAAC_MCP_PYTHON first if the install is outside the searched locations, so it is recorded.
./install.sh --no-sim
ISAAC_SIM_PYTHON=<env>/bin/python
"$ISAAC_SIM_PYTHON" -c 'import importlib.metadata as m, sys; print(sys.version); print(m.version("isaacsim"))'
uv pip check --python "$ISAAC_SIM_PYTHON"
ISAAC_MCP_PYTHON="$ISAAC_SIM_PYTHON" ./run.sh --port 8766
```

Without the override, `run.sh` uses this checkout's `.venv/bin/python` and otherwise searches
the documented locations only ([WORKSPACE.md](WORKSPACE.md#what-is-auto-detected-and-where-it-looks)).
A vendor installation still needs compatible dependencies for these extensions.
A plain Python `import omni` failure outside Kit does not prove the installation is broken.

| Requested build | Installation path | Qualification |
|---|---|---|
| **6.2.0.0** | New pinned checkout above, locked alternative, or explicit existing interpreter | Per-source/mode results in the matrix; inspect dependency conflicts in existing environments |
| **6.0.0.0, 6.0.0.1, 6.0.1.0, 6.1.0.0** | Separately prepared compatible Python 3.12 environment; explicit `ISAAC_MCP_PYTHON` | Scoped historical and follow-up results, not clean-install recipes or latest-fix certification for every mode |
| Other/future builds | No tested setup recipe here | Unverified |

Consult NVIDIA's version-specific [6.0 install guide](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/installation/install_python.html)
or [6.0.1 guide](https://docs.isaacsim.omniverse.nvidia.com/6.0.1/installation/install_python.html)
when obtaining older builds. **Do not run the root 6.2 sync in those environments.**
Run their `uv pip install` from outside this checkout or with `--no-config`: inside it, `uv` applies the
root `pyproject.toml`'s 6.2 overrides (`torch`, `numpy`, `packaging`), which caused false conflicts and a
failing `uv pip check` on 6.0.0.1 and 6.1.0.0 until the environments were rebuilt.
The [compatibility record](COMPATIBILITY.md) is authoritative for measured scope: later save,
viewport and URDF follow-ups have superseded some early pending checks, while the original
6.0.1 windowed native-step stall and remaining matrix gates remain open.

## Prepare the work cell's asset pack

The host connection and `sim__stage__info` do not require a robot or workstation, and NVIDIA's public robots
(`ur5e`, `franka`, `unitree_g1`, ...) load by name from the running simulator's own asset root. The UR5e Jenga work cell uses
[a private asset pack](../exts/ur5e-dt/workcell/README.md#private-asset-pack) (`ur5e-jenga-workcell/v1`) that you supply. The
repository ships **no assets**, `install.sh` installs packages, not assets, and nothing is ever downloaded for you.

Every pack lives in one **asset root**: `ISAAC_MCP_ASSET_ROOT`, else `<workspace>/assets` (a folder named
`isaac-mcp-assets` there still works, with a warning). See [asset packs](ASSET-PACKS.md) for the format and the tools.
Copy the pack folder onto the machine, then check it:

```bash
export ISAAC_MCP_ASSET_ROOT=<asset-root>        # outside Git; set it for the MCP server and for any Kit you start yourself
python3 scripts/isaac_assets.py list              # every <id>/<version> and whether it verifies
python3 scripts/isaac_assets.py doctor ur5e-jenga-workcell/v1
```

`list` also names any pack in the old format (files or a `SHA256SUMS`, no `pack.yaml`), which nothing reads; convert one with
`isaac_assets.py adopt --from-old` ([converting an old pack](ASSET-PACKS.md#converting-an-old-pack)). Setting the root
only in the MCP client cannot change an already-running Kit process's environment: give Kit the same value.

The files remain subject to their own terms (see [third-party notices](../THIRD-PARTY-NOTICES.md) and
[vendoring](VENDORING.md)); the installer's `--accept-eula` controls simulator setup and is not an asset-redistribution
license. Obtain the necessary permission before copying. A missing or unverifiable pack is a real failed operation: it must
not be counted as a loaded robot.

The **Vention bench** has no public download. Supply an approved licensed workstation project through
[workstation preparation](TOOLS-SCENE.md#project-usd-and-the-ur5e-workstation), or put it in a pack with
`isaac_assets.py adopt`. Basic connection and generic scene work require neither.

## Troubleshooting the first connection

| Symptom | Check and next step |
|---|---|
| `isaac-mcp` not found | Add the uv tool bin directory to PATH, or use its absolute executable path in the client; keep the editable checkout available |
| Interpreter missing | Run `python3 scripts/isaac_env.py find`, select a compatible `ISAAC_MCP_PYTHON`, or install in the new checkout; do not overwrite an unrelated environment |
| `uv pip check` fails | Record exact versions and conflicts. Use a new isolated locked environment for comparison; do not infer a native failure's cause from metadata alone |
| 109 tools but runtime calls fail | Catalogs can be offline. Check the advertised bridge, `refresh_tools`, filters and selected endpoint |
| Port already in use / multiple bridges | Verify PID/host/port; reuse the intended bridge or select an unused port for an authorized new session |
| Startup still busy / `NOT_READY` | Inspect startup logs and stage readiness. Camera cache is unseeded until a successful tool capture; ordinary Play does not seed it |
| Missing `omni`, renderer or `.pyi` imports | Verify selected Sim and enabled Kit extensions. Do not pip-install `omni` or hand-copy type stubs |
| `Missing pack ...; sync this private pack first` | The pack is not under the asset root the process resolved. Run `python3 scripts/isaac_assets.py where` and `list`, and give Kit the same `ISAAC_MCP_ASSET_ROOT` |
| `Checksum mismatch` / `Pack contains files not listed` | The pack differs from its `pack.yaml`. Run `isaac_assets.py doctor <id>/<version>`; restore the file, or write a new version. See [asset packs](ASSET-PACKS.md) |
| `No asset root: ...` | Set `ISAAC_MCP_ASSET_ROOT`, or create `<workspace>/assets` |
| `No module named 'Xlib'` when starting the MCP host | You ran the host with Isaac Sim's Python. It has its own dependencies: use the installed `isaac-mcp`, or `cd mcp && uv run --locked --isolated ...` |
| `ModuleNotFoundError: No module named 'mcp'` from a helper script | Scene helpers need the MCP SDK, and the validators `pxr` and `numpy`: `uv run --locked --isolated --project mcp --extra scene-test --python 3.12 python scripts/<name>` from the checkout root. Do not use plain `uv run` in the root |
| Generic edit refuses with `TIMELINE_ACTIVE` after a UR5e Quick Start | Quick Start leaves the timeline playing. Call `sim__timeline__control` with `action="stop"` first |
| `PHYSICS_NOT_READY` from a joint tool | Pass the articulation root, not the robot's parent Xform; for the twin that is `/World/UR5e/root_joint`. See the [typical session](TOOLS-SCENE.md#a-typical-session) |
| `/scan` missing after reopening a saved scene | Open it with `sim__stage__open`, which repairs stale LiDAR products. See [saved LiDAR scenes](TOOLS-SCENE.md#saved-lidar-scenes-and-scan) |
| `refresh_tools` selected another port | It found no bridge on the pinned port and one session this server launched. The result names it; verify with `status` |
| `status` shows `host.stale: true` | This MCP server predates your `git pull`. Restart the MCP connection |
| `SAVE_UNSUPPORTED` | Inspect returned properties/retry guidance; pause and follow the explicit normalization contract in [scene tools](TOOLS-SCENE.md) |
| `TIMELINE_BUSY` or a call stops responding | Preserve logs and identity; do not repeatedly replay Play or assume a client timeout cancels native work. See the known stall in [compatibility](COMPATIBILITY.md) |
| No display / screenshot fails | Use the actual X11/XWayland display for window tools, or headless sensor capture; neither creates a desktop session |

## Python imports and libraries

The checked-in dependency lists are authoritative: [`pyproject.toml`](../pyproject.toml) and
[`uv.lock`](../uv.lock) for the simulator, and [`mcp/pyproject.toml`](../mcp/pyproject.toml) for the host. The
installer handles the dependencies below; they should not be mixed into a system Python by hand.

| Component | Interpreter and imports | How supplied |
|---|---|---|
| Host MCP server | Python 3.10 or newer; `mcp`, `mss`, `PIL` (Pillow), `Xlib` (python-xlib), `imageio_ffmpeg` | `./install.sh` or `./install.sh --no-sim` installs the host package and its dependencies. Screen capture requires an accessible graphical display; window selection/raising uses X11/XWayland. |
| Host Isaac Lab metrics | `tensorboard` | `./install.sh` installs the host with the `isaaclab` extra. If installing the host separately, use the `[isaaclab]` extra for metrics. |
| Isaac Sim and twin | Python 3.12; `isaacsim`, `omni`, `carb`, `pxr`, `numpy`, `scipy`, `yaml` (PyYAML), plus project dependencies including `matplotlib`, `torch` and `torchvision` | The root environment is synced by `./install.sh`; Sim supplies its Kit/USD modules. Many Kit imports are available only after Kit starts, not from an arbitrary Python interpreter. PyYAML reads an optional camera configuration. |
| Simulator renderer and camera runtime | `omni.hydratexture` and `omni.kit.hydra_texture` inside Kit | Bundled NVIDIA extension in the inspected Sim installs; enabled/resolved as part of the Kit runtime where needed. `.pyi` files are type descriptions, not a separate user installation. Do not install an unrelated `omni` package or copy vendor files. |
| Simulator ROS bridge | `rclpy`, `sensor_msgs`, `tf2_msgs` and default message types inside Kit | Supplied by the enabled Isaac Sim ROS 2 bridge; the repository launcher enables it. Custom ROS interfaces need a workspace built for Sim's Python 3.12; see [NVIDIA's ROS setup](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/install_ros.html). |
| Isaac Lab jobs | Isaac Lab 3.0, its Python environment, compatible Isaac Sim, task modules and the selected training library | Install Isaac Lab separately. An activated environment or a checkout-local `.venv` can be discovered; use `ISAACLAB_PYTHON` and `ISAACLAB_ROOT` to select another installation. See [Isaac Lab tools](TOOLS-ISAACLAB.md). |
| External ROS driver | System ROS `ros2`, UR driver, controller CLI, and Cyclone DDS | Install the matching ROS distribution and packages below. The launcher sources the system ROS setup itself. |

For recordings, `install.sh` supplies an `ffmpeg` shim backed by the host's `imageio-ffmpeg` package, unless an
existing `~/.local/bin/ffmpeg` is present. A working system `ffmpeg` can also be used. A windowed workflow needs
an accessible display; host window and input controls were tested with X11/XWayland, not native Wayland.
A headless Sim can still render sensor output but has no visible window to click or capture. Host screen tools have
no mouse or keyboard control by default; the X11 input tools can be explicitly enabled as described in
[host tools](TOOLS-HOST.md#optional-desktop-input).

If a Kit module or renderer extension appears missing, first check the selected Sim interpreter and the
running Kit extension manager/startup log. Do not try to fix a Kit import by installing `omni` or `.pyi` files
into the host Python. The supplied launcher requests `isaac.mcp.bridge`, `sim-tools`, `ur5e-dt` and
`isaacsim.ros2.bridge`. After connecting the correct bridge, `status` and
`sim__stage__info` help verify the running process and build; listing catalog tools by itself does not.

## Optional Lab, desktop controls and project assets

**Isaac Lab** is a separate installation with its own version pairing and interpreter. Set
`ISAACLAB_PYTHON=<workspace>/envs/lab-3.0/bin/python` and
`ISAACLAB_ROOT=<workspace>/IsaacLab` (example locations; use your own) in the host/client environment, then call
`isaaclab__install_info` before launching jobs. Invalid explicit paths fail discovery; they do
not silently install Lab. The host's `isaaclab` extra supplies TensorBoard metrics support,
not the Lab runtime. For the inspected Lab 3 video workflow, MoviePy
`>=1.0.3,<2.0.0.dev0` and `imageio-ffmpeg` belong in the **Lab interpreter**; use that
Lab checkout's video extra in a dedicated compatible environment. Plain headless training
does not require those video packages. Earlier video checks used a private overlay. A later self-contained Lab 3 / Sim 6.2
environment exercised training and video without inherited `.pth` files, but retains two
package-metadata conflicts. See [recording a run](VERIFICATION.md#recording-a-run-so-it-can-be-reproduced)
and [Lab versions, discovery and video modes](TOOLS-ISAACLAB.md); neither is a clean-machine
or full-installer certification.
A successful job launch is not a completed job: inspect status and logs. Jobs normally refuse
alongside an active Kit bridge; coordinate GPU ownership instead of bypassing the guard by default.

**Desktop input and managed sessions** are opt-in host groups. Set
`ISAAC_MCP_LOCAL_TOOLS=all,screen_input,sim_launch` in the client server environment only
when both are needed. Desktop input acts on a shared X11 session; verify window/PID first.
Managed launches require resource checks and distinct endpoints. See [host tools](TOOLS-HOST.md).

**Your project assets** need an explicitly approved trusted folder and a host/Kit-accessible
staging/cache location. Approval can include a bounded project copy, not just one USD file.
Loading native assets is not sandboxed. `assets__prepare_workstation` prepares a private cache
from the operator's approved workstation USD; it does not load the scene or overwrite an
existing cached asset. Vention-specific routing is separate from generic project import.
No private workstation or personal asset is required for the initial read-only connection check.
Follow [project setup and exact tool parameters](TOOLS-SCENE.md#project-usd-and-the-ur5e-workstation)
and [third-party notices](../THIRD-PARTY-NOTICES.md).

## Optional UR5e mock ROS driver

No physical robot, Docker or ROS workspace is required for `backend="fake"`. The driver publishes ROS joint states
and accepts trajectory commands using mock hardware. System ROS is needed for this external driver even though the
simulator bundles libraries for its own bridge.

On Ubuntu 24.04, first follow the [official ROS 2 Jazzy Ubuntu installation](https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html)
to configure ROS packages. Then install the UR driver, controller CLI and Cyclone DDS:

```bash
sudo apt update
sudo apt install ros-jazzy-ur-robot-driver ros-jazzy-ros2controlcli ros-jazzy-rmw-cyclonedds-cpp
```

For Ubuntu 22.04 use the matching `ros-humble-...` packages and Humble setup. The Jazzy combination above was
tested on the machine listed here; the Humble mock-driver path has automated launcher tests but has not been
live-tested as part of this check.

Start the simulator with `./run.sh`, connect the MCP host, and call `ur5e_dt__start_ros_driver` with
`{"mode":"sim","action":"up","backend":"fake"}`. Use `action: "status"` to inspect it and `action: "down"` to
stop it. The launcher uses the default `/opt/ros/jazzy/setup.bash` or `/opt/ros/humble/setup.bash` automatically; `ROS_SETUP`
and `ROS_DISTRO` override that for a nonstandard installation. The same ROS domain and
[`config/ros_dds.env`](../config/ros_dds.env) must be used by external ROS clients. This profile is local-only and
sets Cyclone DDS; source it in a separate external ROS terminal after the ROS setup, rather than mixing an
incompatible ROS Python environment into the simulator's environment.

The fake-driver launcher uses the Linux `setsid`, `ps`, and `timeout` commands. An unmanaged `./run.sh` session
records its mock process in `/tmp/ur_fake_driver.pid` and logs to `/tmp/ur_fake_driver.log`. A `sim__launch`
managed session instead keeps its own driver PID file and log in that session's directory; shutdown verifies both
the mock command and the session marker before signaling the driver. Startup still refuses to start when another UR
driver is detected, even in a different ROS domain. Stop the fake driver before `sim__stop`: the driver's separate
process group is not automatically stopped with the simulator.

`backend="ursim"` is a different, optional workflow requiring Docker and the ROS workspace described in
[the UR5e tool reference](TOOLS-UR5E.md#ur5e_dt__start_ros_driver). Its container and process controls are global,
so it is not available through a managed `sim__launch` session.
