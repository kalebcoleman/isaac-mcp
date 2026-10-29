# ur5e-dt Extension

Isaac Sim extension for UR5e Digital Twin with MCP (Model Context Protocol) integration.

## Features

- **UR5e Robot Control**: Load and configure UR5e robotic arm with action graphs
- **RG2 Gripper**: Import and attach RG2 gripper to UR5e with force feedback
- **Intel RealSense Camera**: Attach camera to robot end effector
- **Object Management**: Add, delete, assemble, and randomize objects in the scene
- **MCP Integration**: Socket server for remote control via MCP protocol

## MCP Tools

This extension exposes the following tools via MCP:

| Tool | Description |
|------|-------------|
| `assemble_objects` | Assemble objects based on assembly JSON file |
| `randomize_object_poses` | Randomize object positions within bounds |
| `save_scene_state` | Save current object poses to JSON |
| `restore_scene_state` | Restore object poses from JSON |
| `clear_scene_state` | Delete the scene state file |

## Socket Server

The extension runs a socket server on port 8766 (configurable via `MCP_SERVER_PORT`).

Commands are sent as JSON:
```json
{
  "type": "command_name",
  "params": {}
}
```

## Configuration

- `MCP_SERVER_PORT`: Socket server port (default: 8766)
- Objects folder: `/World/Objects`
- Assembly file: Configurable via UI
