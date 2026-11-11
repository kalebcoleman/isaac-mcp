# The bridge inside an Isaac Lab script

The bridge needs only Kit, so it also runs in the Kit app an Isaac Lab standalone script starts.
Start from `mcp/templates/isaaclab_bridge_launch.py`. Three things differ from the Isaac Sim app:

- The script enables the bridge itself, through `--kit_args`. What `scripts/install.py` stores
  applies to the Isaac Sim app only.
- Settings come from the environment, set before the app starts (`ISAAC_MCP_PORT`,
  `ISAAC_MCP_TOOL_PATHS`, `ISAAC_MCP_TOOL_MODULES`).
- The script owns the main loop, and the bridge answers only while Kit updates. In Isaac Lab
  3.0.0, `sim.step()` and `env.step()` update Kit only with a windowed Kit visualizer. Headless,
  the port accepts connections but calls that need the main loop never return (seen with
  `sim.step()`; for `env.step()` this is read from the source, not run). So call
  `simulation_app.update()` in your loop, with `/app/player/playSimulations` switched off around
  it as Isaac Lab does, so that Kit does not advance physics a second time. `status` and
  `job_status` answer regardless, because they do not need the main loop.

A Lab script is its own Kit process and needs its own port: the template uses 8767, leaving 8766
to the Isaac Sim app.

The `isaaclab__*` tools are separate from all this: they start Lab scripts as jobs and need no
bridge. See [TOOLS-ISAACLAB.md](../../docs/TOOLS-ISAACLAB.md).
