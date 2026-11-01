# Isaac Sim and Isaac Lab desktop control plan

## Goal and current slice

Give an authorized agent a way to inspect and operate simulator windows and their tabs even when
there is no dedicated semantic MCP tool for a panel. Prefer semantic simulator tools for scene
operations when available; they have clearer targets and results than pixels. The host and Kit
bridge are separate, so desktop control is useful even when a window has no bridge.

The initial implementation adds an explicitly selected `screen_input` group with click, drag,
scroll, typing and key tools for X11. It reuses the existing window list and screenshots. All
input requires an explicit window ID and expected PID; pointer actions require the observed
window size and a visible, uncovered point, and keyboard actions require focus in that window.
The group is absent from the default selection, including the `all` alias. See
[the host reference](../TOOLS-HOST.md#optional-desktop-input) for setup and limitations. The
separate opt-in `sim_launch` group starts and tracks isolated simulator processes with configurable
resource checks. See the [live verification record](../evidence/desktop-control-2026-10-31.md).

These checks reduce common targeting mistakes, not all risk. X11 is a shared global desktop;
another client, input grab or person can change routing. The agent can trigger any application
action, including saving, deleting, starting processes and network operations. A dedicated
desktop/session with one authorized controller is preferable. MCP annotations are advisory.

## Validation and remaining milestones

1. **Initial X11 input:** exercised through MCP in real Isaac Sim 6.2 and Isaac Lab 3.0 windows,
   including tab changes, temporary search input, scrolling and dragging. Targeting, opt-in and
   failure paths have CPU tests. Repeat across other desktop environments and workload sizes.
2. **Targeting and ergonomics:** determine which Kit dialogs and popup menus are separate windows
   or input grabs; support them only with explicit targeting and tests. Consider structured window
   metadata, display scaling, non-US keyboard layouts, Unicode and accessibility-tree discovery.
   Do not infer hidden controls or claim all tabs are controllable until tested.
3. **Parallel sessions:** the initial launcher checks known process count, free memory, ports and
   known ROS domains; separates output; and identifies its own processes. Characterize actual VRAM
   and RAM peaks, rendering/physics throughput, CPU usage and startup spikes for representative
   scenes and Lab jobs. Headroom checks are snapshots, not guarantees. Coordinate agents that
   share one desktop, or isolate their displays. A larger GPU is not an unlimited resource.
4. **Other environments:** investigate native Wayland, virtual displays, remote machines, Windows,
   authentication and permissions as separate backends. Do not assume XWayland grants access to
   native Wayland windows. Test each supported configuration before documenting it as working.

## Evidence to capture

Record the host OS/display type, Isaac Sim and Lab versions, target PIDs/window IDs, enabled
tool groups, input actions and observed application state, including failures and cleanup. For
parallel tests record peak GPU/system memory and whether both workloads remain responsive. Do not
operate on another user's active scene or overwrite its outputs as part of the test.
