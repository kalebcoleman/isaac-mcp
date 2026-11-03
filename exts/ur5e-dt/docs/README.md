# ur5e-dt extension

Isaac Sim extension for the UR5e digital twin. It builds a workstation scene with a UR5e arm, an RG2 gripper
and a RealSense camera, manages objects and scene state, and publishes ROS 2 graphs.

It registers 23 `ur5e_dt.*` tools on the shared `isaac.mcp.bridge` and has no socket server of its own. MCP clients
see them as `ur5e_dt__<tool>` through the `isaac-mcp` host. The supplied `./run.sh` enables the tools and hides
the panel unless you pass `--ur5e-panel`.

- Tools and behavior: [docs/TOOLS-UR5E.md](../../../docs/TOOLS-UR5E.md)
- Arguments: [docs/TOOLS-ARGUMENTS.md](../../../docs/TOOLS-ARGUMENTS.md#ur5e-twin-ur5e_dt__)
- Assets: the repository ships none; see [docs/ASSETS.md](../../../docs/ASSETS.md)
- ROS `rclpy` setup: [rclpy-setup.md](rclpy-setup.md)
- Checks: [docs/VERIFICATION.md](../../../docs/VERIFICATION.md#live-ur5e-baseline)
