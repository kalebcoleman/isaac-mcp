# Generic scene follow-up: M4 and M5 evidence

Live runs of the M4 joint-control tools and the M5 any-prim editing tools, through a real stdio MCP
client (`scripts/scene_mcp_client.py` and the milestone scripts below), against disposable headless
Isaac Sim sessions. No `run_python`, bridge API or private Kit API was used for the assertions.

- [M4](M4/README.md): `sim__joints__command` (name targets, velocity targets, clamp), `sim__joints__gains`,
  `sim__joints__move`, `sim__joints__teleport`.
- [M5](M5/README.md): `sim__prim__set_transform`, `sim__prim__set_attribute`, `sim__physics__apply`,
  `sim__prim__revert`, and `sim__prim__create` with `Plane`, `mass` and `density`.

## Environment

| | 6.2 run | 5.1 run |
|---|---|---|
| OS, display | Ubuntu Linux, headless (no X server needed) | same host |
| GPU, driver | NVIDIA GeForce RTX 5080, 16 GB | same host |
| Isaac Sim | 6.2.0.0 (`Isaac-Sim Python`, headless) | 5.1.0 (`Isaac-Sim Python`, headless) |
| Backend | PhysX | PhysX |
| Kit PID / port | disposable Kit on port 18901, stopped by PID after the runs | disposable Kit on port 18901, stopped by PID after the runs |
| isaac-mcp | branch `t3/cb449f79` at the commits named in the milestone READMEs | same checkout |
| Tool groups | `ISAAC_MCP_LOCAL_TOOLS=all`, `ISAAC_MCP_BUILTINS=1` | same |
| Assets | empty `ISAAC_MCP_ASSET_PATH`; robots from this simulator's own assets root; one USD written by the M5 script under the scene output root | same |
| ROS | `ROS_DOMAIN_ID=111` | same |

Both Kits were started with
`ISAAC_MCP_PYTHON=<python> ISAAC_MCP_ASSET_PATH=<tmp>/assets ISAAC_MCP_SCENE_OUTPUT=<tmp>/out
OMNI_KIT_ACCEPT_EULA=YES ROS_DOMAIN_ID=111 ./run.sh --headless --port 18901` and stopped with
`kill -TERM <Kit PID>`; no other session on the machine was touched.

## Scripts

- `scripts/validate_scene_m4_joints.py --robot ur5e|franka`
- `scripts/validate_scene_m5_edit.py --directory <output-root> --robot franka`

Both write their full JSON call transcript to `--output` and print a compact JSON summary; the
tables below are from those summaries. Full transcripts stay out of Git (they contain long tool
payloads); the environment and results needed to reproduce them are recorded here.

## What was not verified

Isaac Sim 6.0.0.0, 6.0.0.1, 6.0.1.0 and 6.1.0.0 were not available on this workstation, so no M4/M5
result is claimed for them. The adapters use the same tensor-view API names that 5.1 and 6.2 expose;
a build that differs refuses with `PHYSICS_API_UNAVAILABLE` / `PHYSICS_FORMAT` before changing
anything. Newton was not exercised for M4/M5 on this host; the tools take the same tensor-view path
that `sim__joints__state` already uses and refuse unqualified Newton builds through the existing
`physics.manager()` check. No screenshots or videos are included: both runs were headless, and this
milestone's proof is numeric. The M4 "blocked by contact" proof is the real ground contact of a public
UR5e in its zero pose (`shoulder_lift_joint` pressing into the floor); the same result is also shown
deterministically with a zero-stiffness drive. A windowed GUI check, Newton, and the 6.0/6.1 builds
remain unverified.
