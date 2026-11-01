# Desktop-control and parallel-session verification — 2026-10-31

These are local observations, not a claim that every Kit panel, operating system or workload is
supported. Input was sent through a real MCP client/server session to X11 windows, not a synthetic
window. Screenshots were inspected after each change. The temporary test Sim and Lab processes
and their outputs were separate from the existing BrickSim session.

## Test environment

- Ubuntu 24.04.5, X11 display `:0`, NVIDIA RTX 5080 with 16,303 MiB reported by `nvidia-smi`,
  driver 580.178.04, approximately 62 GiB system RAM.
- Isaac Sim 6.2.0 and Isaac Lab 3.0.0; a separate Isaac Sim 5.1 BrickSim bridge was present on
  port 8877 during the two-process tests. Sim used port 18766 and ROS domain 197; the Lab job
  used domain 198. The Lab test used a temporary one-environment policy playback and checkpoint.
- Input and session-management tools were explicitly enabled. The windows were selected by X11
  client ID and expected PID; all captures and test outputs were written to temporary directories.

## Observed results

- In Sim, `raise_window` activated the client window. `window_click` switched between Stage and
  UR5e Digital Twin tabs. `window_type` entered `Test A1!` in the Stage search field;
  `window_key` with Ctrl+A and Backspace cleared it. `window_scroll` moved the twin panel from
  its top to the Objects section, and `window_drag` moved its scrollbar back up. Screenshots
  confirmed the visible changes.
- In Lab, `window_click` switched from Stage to Layer, `window_type` entered `World42` in the
  Layer search field, and `window_key` cleared it. The UI displayed the entire text several
  seconds after the input call returned. `window_drag` moved the panel divider, and
  `window_scroll` changed the viewport zoom. Screenshots confirmed the resulting UI.
- X11 reused the former Sim client ID for the new Lab window. A call with the old Sim PID was
  refused, rather than sent to Lab. A covered target and a stale window size were also rejected
  during the Sim test. `raise_window` initially failed to bring a covered client forward; the
  implementation was changed to request and verify window-manager activation. A later live
  check rejected a mismatched expected PID, then activated the intended client with the right PID.
- Through MCP, `sim__launch` started a 6.2 process alongside BrickSim, `sim__status` matched
  its bridge and PID, and `sim__stop` stopped that managed process. `sim__list` found active and
  earlier exited managed sessions from another server process. A third launch at the default
  two-process limit was rejected; an occupied port, a known ROS-domain conflict, and an
  intentionally raised free-VRAM threshold were also rejected. A running Lab job in the
  configured job directory counted toward the limit.
- After BrickSim was closed by its operator, two independently launched 6.2 windows reached
  `running` on ports 18766 and 18767, with different ROS domains, output directories and Kit
  persistent settings files. With one recording, the other reported `Not recording`. Both then
  recorded simultaneously at 5 fps; each produced a separate 25-frame, five-second MP4 that
  decoded successfully. Stopping one recorder did not stop the other. Stopping one simulator
  left the other bridge responsive. A later run also verified normal pidfd shutdown, explicit
  force shutdown, and refusal of a mismatched PID or missing discard confirmation.
- At one point BrickSim used about 2,841 MiB of GPU memory, an empty 6.2 Sim about 1,826 MiB,
  and the separate Lab playback about 4,787 MiB. These are snapshots, not maximum consumption.
  A three-process attempt left the second empty 6.2 process unresponsive during startup for
  more than two minutes while it used substantial CPU; it was stopped. No reliable three-process
  capacity is claimed. The operator subsequently closed BrickSim; its exit was user-initiated.
- A repeat with three disposable 6.2 processes and the count limit explicitly set to three
  reached a responsive bridge on all three ports (18766–18768). Observed startup waits after
  each launch were 7.3, 7.7 and 7.8 seconds; the last readiness snapshot reported 10,596 MiB
  free GPU memory and 36,113 MiB available system memory. All three processes were stopped
  afterward. These were empty sessions; this does not establish rendered-frame health or capacity
  for three populated scenes, sensors, or training jobs.

The desktop input results demonstrate the tested controls, not every UI control. X11 remains a
shared desktop, and its target checks cannot protect against every other X client, input grab,
or simultaneous human action. Headroom checks do not reserve resources. Native Wayland,
multi-GPU launch selection, real hardware, high-sensor workloads and broad parallel-training
capacity were not validated by these tests.

## Optional twin and docked panel follow-up

- A fresh graphical `sim__launch` without `ur5e_dt` reached a responsive 6.2 bridge. Its extension
  manager had no twin panel and the MCP client saw no `ur5e_dt__*` tools. The configured sessions
  directory was relative to the MCP server's working directory; the returned log, output and
  Kit user-configuration paths were absolute and under that directory.
- In that same process, enabling `ur5e-dt` through Kit's extension manager and calling
  `refresh_tools` exposed 23 twin tools. `ur5e_dt__get_recording_status` returned success. A
  screenshot showed the twin as a tab beside Console, and the Kit workspace reported the same
  dock ID for both windows. Disabling the extension and refreshing removed all 23 tools.
- A second fresh graphical launch with `ur5e_dt=true` reached a responsive bridge with all
  23 twin tools already registered. Its panel also shared Console's dock ID. Kit reported its
  user configuration path under the same resolved session directory; a six-second, 30-frame
  recording was written under that session's output directory and decoded successfully with
  `imageio-ffmpeg` (the separate `ffprobe` executable was not installed). Both temporary processes
  were stopped through `sim__stop`.
- Separate headless 6.2 sessions reached responsive bridges with zero and 23 twin tools for
  `ur5e_dt=false` and `true`, respectively. The enabled session returned recording status.
  Both were stopped through `sim__stop`. These checks did not build a robot scene or exercise
  other Kit versions.
