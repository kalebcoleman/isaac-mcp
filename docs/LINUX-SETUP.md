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

`./install.sh` uses `uv` to create the Python 3.12 simulator environment and install the host server. It does not
install Isaac Lab or the system ROS driver. Use `./install.sh --no-sim` to install only the host server; `run.sh`
requires a separate Sim environment. `run.sh --headless` omits the visible simulator window. The checker also warns
about an absent simulator environment when it has not yet been installed.

## Python imports and libraries

The checked-in dependency lists are authoritative: [`pyproject.toml`](../pyproject.toml) and
[`uv.lock`](../uv.lock) for the simulator, and [`mcp/pyproject.toml`](../mcp/pyproject.toml) for the host. The
installer handles the dependencies below; they should not be mixed into a system Python by hand.

| Component | Interpreter and imports | How supplied |
|---|---|---|
| Host MCP server | Python 3.10 or newer; `mcp`, `mss`, `PIL` (Pillow), `Xlib` (python-xlib), `imageio_ffmpeg` | `./install.sh` or `./install.sh --no-sim` installs the host package and its dependencies. Screen capture requires an accessible graphical display; window selection/raising uses X11/XWayland. |
| Host Isaac Lab metrics | `tensorboard` | `./install.sh` installs the host with the `isaaclab` extra. If installing the host separately, use the `[isaaclab]` extra for metrics. |
| Isaac Sim and twin | Python 3.12; `isaacsim`, `omni`, `carb`, `pxr`, `numpy`, `scipy`, `yaml` (PyYAML), plus project dependencies including `matplotlib`, `torch` and `torchvision` | The root environment is synced by `./install.sh`; Sim supplies its Kit/USD modules. Many Kit imports are available only after Kit starts, not from an arbitrary Python interpreter. PyYAML reads an optional camera configuration. |
| Simulator ROS bridge | `rclpy`, `sensor_msgs`, `tf2_msgs` and default message types | Supplied by the enabled Isaac Sim ROS 2 bridge. Custom ROS interfaces need a workspace built for Sim's Python 3.12; see [NVIDIA's ROS setup](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/install_ros.html). |
| Isaac Lab jobs | Isaac Lab 3.0, its Python environment, compatible Isaac Sim, task modules and the selected training library | Install Isaac Lab separately. An activated environment or a checkout-local `.venv` can be discovered; use `ISAACLAB_PYTHON` and `ISAACLAB_ROOT` to select another installation. See [Isaac Lab tools](TOOLS-ISAACLAB.md). |
| External ROS driver | System ROS `ros2`, UR driver, controller CLI, and Cyclone DDS | Install the matching ROS distribution and packages below. The launcher sources the system ROS setup itself. |

For recordings, `install.sh` supplies an `ffmpeg` shim backed by the host's `imageio-ffmpeg` package, unless an
existing `~/.local/bin/ffmpeg` is present. A working system `ffmpeg` can also be used. Host screen tools have no
mouse or keyboard control by default; the X11 input tools can be explicitly enabled as described in
[host tools](TOOLS-HOST.md#optional-desktop-input).

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

The fake-driver launcher uses the Linux `setsid`, `ps`, and `timeout` commands. It records its mock process in
`/tmp/ur_fake_driver.pid`; shutdown checks that process before signaling it and does not globally stop unrelated UR
drivers. Its log is `/tmp/ur_fake_driver.log`.

`backend="ursim"` is a different, optional workflow requiring Docker and the ROS workspace described in
[the UR5e tool reference](TOOLS-UR5E.md#ur5e_dt__start_ros_driver).
