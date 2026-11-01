# Linux setup and dependencies

This guide covers the repository's Linux launcher, host MCP server, optional Isaac Lab tools, and optional ROS 2
UR5e mock driver. Install the parts needed for your workflow; the host server, simulator and external ROS nodes run
in separate environments.

## Platforms and prerequisites

The full simulator installer targets **Ubuntu 24.04 or 22.04 on x86_64**. It selects ROS 2 Jazzy on 24.04 and
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

From the repository root:

```bash
scripts/check_machine.sh
./install.sh --accept-eula
./run.sh
```

`./install.sh` requires [uv](https://docs.astral.sh/uv/getting-started/installation/) to be installed first.
It creates the pinned 6.2 Python 3.12 simulator environment and installs the host server, including the host's
TensorBoard metrics extra. It does **not** install Isaac Lab or system ROS. Use `./install.sh --no-sim` for the
host only; `run.sh` still needs a compatible separate Sim environment. `run.sh --headless` omits the visible
window but still requires a compatible NVIDIA GPU and driver. The checker warns if Sim is not installed; if an
interpreter exists, it expects exactly 6.2.0.0 and launches a small Torch CUDA kernel. It is not a version-neutral
6.x or CPU-only diagnostic.

## Choose a simulator build

The full repository installer and lock select **6.2.0.0 only**. Earlier builds below were exercised in separate,
previously prepared environments on the recorded Ubuntu 24.04.5 machine. They were not certified as clean installs
by the repository installer. The final combined-PR save/reopen and broader cross-mode qualification remain pending;
see [compatibility](COMPATIBILITY.md) and [per-release evidence](evidence/project-scene/RUNS.md) for the exact scope.

| Isaac Sim Python package | How to select it for this repository | What the existing evidence establishes |
|---|---|---|
| `6.0.0.0` | Existing compatible Python 3.12 environment; set `UR5E_DT_PYTHON` below. | Selected workflows exercised in both modes; earlier reopened-camera evidence was incomplete; final-candidate reopen qualification remains pending. |
| `6.0.0.1` | Existing compatible Python 3.12 environment; set `UR5E_DT_PYTHON` below. | Selected workflows exercised in both modes; final combined-candidate qualification pending. |
| `6.0.1.0` | Existing compatible Python 3.12 environment; set `UR5E_DT_PYTHON` below. | Selected workflows exercised in both modes; final combined-candidate qualification pending. |
| `6.1.0.0` | Existing compatible Python 3.12 environment; set `UR5E_DT_PYTHON` below. | Selected workflows exercised in both modes; final combined-candidate qualification pending. |
| `6.2.0.0` | `./install.sh --accept-eula` installs the pinned environment, or select a compatible existing one. | Baseline workflows exercised; the candidate save/reopen fix and final matrix are pending qualification. |
| Other or future builds | No automatic compatibility claim or tested install recipe. | Unverified. |

To use an existing environment without syncing the repository's 6.2 lock into it:

```bash
./install.sh --no-sim  # installs the host; does not install or replace the Sim environment
ISAAC_SIM_PYTHON=/absolute/path/to/sim-env/bin/python
"$ISAAC_SIM_PYTHON" -c 'import importlib.metadata as m, sys; print(sys.version); print(m.version("isaacsim"))'
uv pip check --python "$ISAAC_SIM_PYTHON"
UR5E_DT_PYTHON="$ISAAC_SIM_PYTHON" ./run.sh --port 8766  # use an unused port; add --headless if desired
```

The package check and `uv pip check` identify the interpreter and dependency conflicts; they do not prove GPU,
renderer, ROS, sensor or every-tool compatibility. Check the running bridge's PID/version and app build as described
in [compatibility](COMPATIBILITY.md#checking-an-installation). For obtaining an older Sim, consult NVIDIA's
[6.0 Python install guide](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/installation/install_python.html) or
[6.0.1 guide](https://docs.isaacsim.omniverse.nvidia.com/6.0.1/installation/install_python.html); a vendor Sim
installation alone is not proof that this repository's additional dependencies resolved correctly. The older test
environments were corrected independently; no universal clean-install command for them is claimed here.

For the supplied launcher, the packaged `isaacsim.ros2.bridge` is enabled even when no external ROS CLI/driver is
installed. System ROS is needed only when an external ROS workflow calls for it. Isaac Lab is a separate optional
installation; choose its Sim pairing from the [Lab matrix](COMPATIBILITY.md#isaac-lab-ros-and-platforms).

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

## Optional UR5e mock ROS driver

No physical robot, Docker or `~/ros2_ws` is required for `backend="fake"`. The driver publishes ROS joint states
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
stop it. The launcher uses `/opt/ros/jazzy/setup.bash` or `/opt/ros/humble/setup.bash` automatically; `ROS_SETUP`
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
