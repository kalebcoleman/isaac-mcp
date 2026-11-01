# Simulator compatibility probes — 2026-10-31

These observations were made on Ubuntu 24.04.5 x86_64, Python 3.12.3, an NVIDIA RTX 5080 and driver 580.178.04.
The initial runtime code was PR #9 at `013dd408`; follow-up runs included the Kit-readiness and 6.0.0.0 transform
graph fixes in this PR, and the smoke script was changed to accept the expected app-version prefix. Tests used
separate disposable simulator sessions, ports, ROS domains, output directories and Kit user
configuration files. They did not use a physical robot. This is not a clean-install certification for all systems.

| Python package | Environment used for this probe | Result |
|---|---|---|
| 6.2.0.0 | Existing environment plus a separate copy corrected to NumPy 2.3.1 | The existing environment's windowed follow-up passed 20/20 smoke checks, hid the panel by default, and received object and camera transforms through separate ROS 2 Jazzy subscribers. The corrected copy passed the same headless scene and ROS checks. At domain 232 Cyclone DDS logged attempts to reach UDP port 65536 despite delivering the messages; a repeat on domain 220 also received both topics without that warning. Managed launches now reject domain 232 under the bundled profile. Earlier windowed and headless tests exercised the optional panel, recordings and managed sessions; see [desktop evidence](desktop-control-2026-10-31.md). |
| 6.1.0.0 | Separate environment; NumPy 2.3.1, `warp-lang` 1.20.0.dev20261031 | An early Quick Start call did not return and the disposable process was force-stopped. Later headless and windowed runs waited for app startup and passed 20/20 smoke checks; a 2-second, 10-fps MP4 decoded to 20 distinct frames. The optional panel was docked beside Console, an MCP host proxy worked, and separate ROS 2 Jazzy subscribers received object and camera transforms. Single-object randomization reported the known crowded-placement warning. Kit logged asyncio task errors and a `NoneType.GetCamera` exception on reset. The subsequent Quick Start returned success; post-reset rendered frames were not checked. |
| 6.0.1.0 | Separate corrected environment; NumPy 2.3.1, `warp-lang` 1.13.0 | The initial environment had dependency conflicts and timed out during tool discovery. After correction, a plain Sim control advanced ten frames without the MCP; headless and windowed sessions each passed 20/20 smoke checks. A 2-second, 10-fps MP4 decoded to 20 distinct frames. The optional panel was docked beside Console, an MCP host proxy worked, and separate ROS 2 Jazzy subscribers received object and camera transforms. Single-object randomization reported the known crowded-placement warning; Kit logged asynchronous task errors. |
| 6.0.0.1 | Separate corrected environment; NumPy 2.3.1, `warp-lang` 1.20.0.dev20261031 | After correcting the initial dependency conflicts, one headless startup took about 132 seconds. Headless and windowed runs passed 20/20 smoke checks; a 2-second, 10-fps recording decoded to 20 distinct frames. An MCP host proxy worked and separate ROS 2 Jazzy subscribers received object and camera transforms in domain 231 without the invalid-port warning observed at 232. In one run an extension reloaded after a source edit and the visible twin panel was no longer docked; a fresh launch without edits seated it beside Console. Single-object randomization reported the known crowded-placement warning; Kit logged asynchronous task errors. |
| 6.0.0.0 | Separate corrected environment; NumPy 2.3.1, `warp-lang` 1.20.0.dev20261031, PyTorch 2.10.0+cu128 | The initial conflicting environment stalled. After correction, a headless run passed 19/20 smoke checks: object-pose publishing failed because the release lacks the later transform-computation node. The compatibility fix used its older publisher, after which headless and windowed runs passed 20/20; the panel was docked beside Console, an MCP host proxy worked, and separate ROS 2 Jazzy subscribers received object and camera transforms in both modes. Kit logged asynchronous task errors. |

The initial 6.0 environment commands inherited this checkout's `uv` dependency overrides. The affected 6.0
environments were corrected outside the checkout with `uv pip install --no-config`; `uv pip check` then reported no dependency
conflicts. The environments were resolved with prereleases allowed, so the selected dependency
sets are part of the result. These observations do not by themselves locate the cause of a stall or rule out a
different dependency set or machine. The smoke script does not exercise physical hardware, a real pose publisher,
every tool argument, or Isaac Lab; a passing count does not cover those paths.

In a 6.0.0.0 startup, managed status reported `starting_or_unresponsive` while the bridge was not responding; on a
6.0.1.0 run it reported `running` after Kit reported ready. Targeted host tests also cover a reachable bridge that
reports `app_ready=false` and one that cannot report readiness.

The earlier 6.2 lock selected NumPy 2.5.4 even though the installed `isaacsim-kernel` wheel declares
`numpy==2.3.1`; `uv pip check` reported that mismatch. The updated lock selects 2.3.1 and preserves the existing GPU
runtime packages. A separate environment copy passed the live headless checks, `uv sync --locked` and
`uv pip check`. The user's existing environment was left unchanged.

An earlier 5.1.0.0 probe used a separate Python 3.11.16 environment. The 23 tools registered, but Quick Start failed
with `ROS2Context` under the default launcher, which points at the 6.x `isaacsim.ros2.core` library location. With a
temporary path to 5.1's bundled `isaacsim.ros2.bridge` ROS libraries, Quick Start and stop returned success and both
hidden and visible panel modes were observed. The log also contained asyncio task errors. ROS publication, physics,
recording and the full tool set were not verified in that probe.
