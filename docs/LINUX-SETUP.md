# Linux setup and dependencies

This guide covers the repository's Linux launcher, host MCP server, optional Isaac Lab tools, and optional ROS 2
UR5e mock driver. Install the parts needed for your workflow; the host server, simulator and external ROS nodes run
in separate environments.

**Keep it organized.** Put the checkout, asset stores and Sim environments in one workspace
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
./install.sh --accept-eula     # skips the download if an Isaac Sim 6.x is already installed; --download-sim forces it
export PATH="$HOME/.local/bin:$PATH"
uv pip check --python .venv/bin/python
.venv/bin/python -c 'import importlib.metadata as m, sys; print(sys.version); print(m.version("isaacsim"))'
```

`main` is the development branch, not a released stable tag. Record its exact
`git rev-parse HEAD` for reproducibility. Expect Sim **6.2.0.0** and Python **3.12**.
Stop and inspect any dependency-check failure before treating the environment as ready.

The convenience installer:

- Runs **`uv sync --python 3.12` in the current directory**. It may update the lock/environment;
  it does not enforce `--locked` and does not use `ISAAC_MCP_PYTHON` (or its alias `UR5E_DT_PYTHON`) as its install target.
- Runs `uv tool install --force --editable` for the host with the `isaaclab` metrics extra.
  This replaces the user's existing `isaac-mcp` tool and ties it to this checkout. Keep the checkout.
- Creates or refreshes its `~/.local/bin/ffmpeg` shim, preserving unrelated files/symlinks.
- Changes client configuration **only** when requested with registration flags.
  `--claude-code` replaces the user-scope `isaac` entry and removes the legacy `twin` entry;
  `--claude-desktop` updates an existing Desktop config with a backup. `--claude` requests both.
  `--print-opencode` prints configuration without writing it. Missing clients/configs can be skipped;
  inspect installer output and use the [manual client guide](MCP-SERVER.md#4-client-configuration).
- Does not install system ROS, Isaac Lab or private workstation assets.

For a lock-preserving alternative, follow [locked setup](REPRODUCIBILITY.md#locked-simulator-setup).
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
it keeps the UR panel hidden unless `--ur5e-panel` is supplied. Its default backend is **PhysX**.
The optional Newton flag is not a compatibility promise; see the [matrix](COMPATIBILITY.md).

For a windowless GPU session, use `./run.sh --headless --port 8766` instead. Headless Sim still
requires the NVIDIA runtime and can render sensors, but has no application window for desktop
capture/input. Windowed tools were exercised on X11/XWayland; native Wayland is not qualified.
Keep the launch terminal available for startup errors. First startup can take minutes for shader
and extension preparation; wait for bridge readiness and `app_ready=true`, not a fixed sleep.
See [startup expectations and recorded scopes](REPRODUCIBILITY.md#what-has-actually-been-verified).

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
BRIDGE_HOST=127.0.0.1
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
The [compatibility record](COMPATIBILITY.md) is authoritative for measured scope: later save,
viewport and URDF follow-ups have superseded some early pending checks, while the original
6.0.1 windowed native-step stall and remaining matrix gates remain open.

## Prepare the UR5e and sensor assets

The host connection and `sim__stage__info` do not require a robot or workstation. A UR5e
Quick Start does need the robot, gripper and object assets. The repository ships **no
assets**: every asset lives in your local asset folder, `isaac-mcp-assets` next to the
checkout (see [ASSETS.md](ASSETS.md)). `install.sh` installs packages, not assets. Add your
own files with `python3 scripts/isaac_assets.py add`, move assets from an older checkout with
`python3 scripts/isaac_assets.py migrate`, and download NVIDIA's public robot and sensor files
once (they are not downloaded again on every launch):

```bash
python3 scripts/fetch_ur5e_assets.py
python3 scripts/fetch_ur5e_assets.py --check
```

The first command downloads missing/mismatched manifest files from the configured NVIDIA
asset root and skips already valid files. The second reads the cache only: exit 0 means all
manifest fetch entries match size/SHA-256; exit 1 reports missing or mismatched entries.
It checks neither the manual Vention bench nor native rendering/physics readiness.
Do not repair or replace a cache while another simulator or user is relying on its files.

The assets remain subject to NVIDIA's applicable terms; see [asset notices](provenance/ASSETS.md)
and [vendoring](VENDORING.md). The fetcher has **no `--accept-eula` flag or interactive EULA
acceptance step**. The installer's `--accept-eula` controls simulator setup; it is not an
asset-redistribution license. Obtain the necessary permission before downloading or copying.

### Asset folder location and supported options

The default asset folder is `isaac-mcp-assets` next to the checkout, or
`<workspace>/isaac-mcp-assets` when `ISAAC_MCP_WORKSPACE` is set. Older locations
(`<workspace>/assets`, `~/.cache/ur5e-dt/assets`) are still read if they exist. You can
override it: `ISAAC_MCP_ASSET_PATH` sets the stores (a list separated by `:`, the first is
where files are written; `UR5E_DT_ASSET_CACHE` is a deprecated alias). Use the same stores in the
fetcher's environment and the selected **Kit process**; setting it only in the MCP client
cannot change an already-running Kit process's environment. Stores, the per-project
`isaac-mcp.json`, user pins and `scripts/isaac_assets.py doctor` are described in
[ASSETS.md](ASSETS.md).

```bash
# Optional: choose another asset folder, outside Git; prepare it before launching a new session.
export ISAAC_MCP_ASSET_PATH=<asset-folder>
python3 scripts/fetch_ur5e_assets.py --check
# If preparation is needed and authorized, fetch into this same cache:
python3 scripts/fetch_ur5e_assets.py
python3 scripts/fetch_ur5e_assets.py --check
# After a successful check, launch the intended new session using local assets only:
ISAAC_MCP_ASSET_STRICT=1 ./run.sh --port 8766
```

The initial check may return 1 on a new cache; that is a missing-asset result, not success.
All options can be inspected without fetching: `python3 scripts/fetch_ur5e_assets.py --help`.

| Option | Scope |
|---|---|
| `--cache-dir PATH` | Overrides the destination for this invocation only; select the same directory through `ISAAC_MCP_ASSET_PATH` when launching Kit |
| `--asset-root URL_OR_DIRECTORY` | Overrides the source for this invocation; an existing local mirror must have the manifest's source-relative layout. `ISAAC_MCP_ASSET_REMOTE_ROOT` is the environment equivalent (`UR5E_DT_ASSET_ROOT` is a deprecated alias) |
| `--check` | Verifies existing cache bytes only; no download or repair |
| `--force` | Re-fetches/copies even matching files; intentional replacement, not a normal startup requirement |

The [manifest](../exts/ur5e-dt/assets-manifest.json) pins the **Isaac asset-library 5.1**
paths and byte hashes. That asset generation is separate from the selected **Sim 6.2**
package version; do not substitute an arbitrary asset-library version. The sensor cache
preserves its source-relative MDL/texture layout. Keep the whole fetched group intact,
rather than moving only `rsd455.usd` into a different folder.

### Local reuse, remote fallback and failures

Resolution prefers a local file in a project store (`isaac-mcp.json`), then the user stores
(the asset folder), then an allowed configured NVIDIA URL for supported robot/sensor entries.
Files under an old checkout's `exts/ur5e-dt/assets` are not used; `isaac_assets.py migrate`
moves them.
Cached manifest entries **and their fetch-group dependencies** are verified before use.
Valid local files are reused. Missing/corrupt group members refuse instead of silently
bypassing verification with a network URL.

If no local top-level asset exists and offline mode is disabled, the resolver can give Kit
the configured remote URI. This is not the same as populating and verifying the operator
cache; it requires network access and does not prove the remote load succeeded. Prefer
explicit prefetch plus `--check` when preparing an offline or repeatable run.

`ISAAC_MCP_ASSET_STRICT=1` (deprecated alias `UR5E_DT_OFFLINE`) disables this twin resolver's remote fallback. With the robot absent,
Quick Start returns **`Asset not available offline: robot/ur5e.usd`** and preparation guidance.
That is a real failed operation; it must not be counted as a loaded robot. Offline mode does
not firewall Kit, arbitrary USD references or other extensions, and it does not prevent an
explicit fetcher invocation from downloading. For a fully offline preparation, use a local mirror.

The **Vention bench is separate**: no public download is configured. Supply an approved
licensed workstation project through [workstation preparation](TOOLS-SCENE.md#project-usd-and-the-ur5e-workstation),
or add your own export with `python3 scripts/isaac_assets.py add` (see [ASSETS.md](ASSETS.md)),
or use the twin's placeholder bench when the manual asset is absent. A placeholder does not
replace the mandatory UR robot asset. Basic connection and generic scene work require neither.

## Troubleshooting the first connection

| Symptom | Check and next step |
|---|---|
| `isaac-mcp` not found | Add the uv tool bin directory to PATH, or use its absolute executable path in the client; keep the editable checkout available |
| Interpreter missing | Run `python3 scripts/isaac_env.py find`, select a compatible `ISAAC_MCP_PYTHON`, or install in the new checkout; do not overwrite an unrelated environment |
| `uv pip check` fails | Record exact versions and conflicts. Use a new isolated locked environment for comparison; do not infer a native failure's cause from metadata alone |
| 83 tools but runtime calls fail | Catalogs can be offline. Check the advertised bridge, `refresh_tools`, filters and selected endpoint |
| Port already in use / multiple bridges | Verify PID/host/port; reuse the intended bridge or select an unused port for an authorized new session |
| Startup still busy / `NOT_READY` | Inspect startup logs and stage readiness. Camera cache is unseeded until a successful tool capture; ordinary Play does not seed it |
| Missing `omni`, renderer or `.pyi` imports | Verify selected Sim and enabled Kit extensions. Do not pip-install `omni` or hand-copy type stubs |
| `Asset not available offline` / corrupt cache | Confirm the selected Kit cache, run the fetcher's read-only `--check`, and prepare/repair it only when safe. A known deliberate harness failure has separate recovery evidence; an ordinary missing asset is still a failure |
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
`isaacsim.ros2.bridge`; the UR5e panel is optional. After connecting the correct bridge, `status` and
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
package-metadata conflicts. See [exact reproduction inputs and limits](REPRODUCIBILITY.md#self-contained-lab-3-reproduction)
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
and [asset license notices](provenance/ASSETS.md).

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
