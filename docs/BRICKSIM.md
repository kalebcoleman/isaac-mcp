# BrickSim with these tools

[BrickSim](https://github.com/intelligent-control-lab/BrickSim) simulates LEGO bricks and how they connect, on Isaac Sim 5.1. These tools work next to it with no change to BrickSim: you start BrickSim as usual and add the bridge to its command. Everything here was run on Sim 5.1.0 with an RTX 5080, on 2026-11-06.

## Run it with the bridge

BrickSim's own launcher accepts extra extensions. From a BrickSim checkout, using its own environment:

```bash
ISAAC_MCP_PORT=8766 OMNI_KIT_ACCEPT_EULA=YES .venv/bin/bricksim \
    --ext-folder <isaac-mcp>/exts --enable isaac.mcp.bridge --enable sim-tools demos/demo_assembly.py
```

Use `.venv/bin/bricksim`, not `uv run`, so that nothing in its environment is changed. Add `--enable ur5e-dt` only if you want the twin's tools. The window opens with BrickSim's scene, 69 tools answer, and `status` reports app version 5.1.0.

## Start a demo from an agent

`demos/demo_assembly.py` builds its scene and then waits, paused, for Play. A plain `sim__timeline__control action=play` refuses with `TIMELINE_ALREADY_ACTIVE`, and its suggested stop would end the demo. Continue it with:

```
sim__stage__info
sim__timeline__control action=play resume=true
```

The arm then builds the structure by itself: all four bricks onto the baseplate, in about four minutes. Scene edits stay refused while it runs, because physics writes where the demo set it, not to the session layer.

## Which tools work on bricks

Bricks are under `/World/managed/Parts/Part_N`, and the arm is `/World/Robot`.

| Tool | Result |
|---|---|
| `sim__stage__tree root=/World/managed depth=3`, `sim__prim__find`, `sim__prim__get`, `sim__prim__bounds` | Work |
| `sim__body__state` on a brick | Works, while the timeline runs |
| `sim__physics__contacts paths=[...]` | Works: names the brick's colliders and what they touch |
| `sim__physics__raycast` | Works |
| `sim__joints__state`, `sim__arm__fk` on `/World/Robot` | Work; the FK self-check agreed with physics to 2e-7 m |
| `sim__camera__list`, `screenshot`, `start_screen_recording` | Work |
| `sim__viewport__frame path=/World/LegoWorkspace` | Refused: that mesh is not a framing target. Give `eye` and `target` |
| `sim__arm__ik move=true`, `sim__joints__command` | Do not use while a demo runs: its own controller is commanding the arm |

These tools see rigid bodies and contacts. What BrickSim adds, such as which studs are connected and its breakage model, lives in its own Python API (`bricksim.core`), which there is no tool for: reach it with `run_python`, which runs code inside Kit.

## Its reinforcement learning task

BrickSim's `Lego-AssembleBrick-v0` has an FR3 arm reach, grasp, lift, align and insert a brick, and it ships no training settings. `examples/bricksim_rl` adds PPO settings and registers `Lego-AssembleBrick-PPO-v0`; nothing in BrickSim changes. Use the Isaac Lab 2.3.2 environment, which has the libraries (same Python 3.11 and Sim 5.1). In the MCP server's environment:

```
ISAACLAB_PYTHON=<lab-2.3.2 env>/bin/python
ISAACLAB_ROOT=<lab-2.3.2 checkout>
ISAACLAB_TASK_PATH=<isaac-mcp>/examples:<BrickSim>/python
ISAACLAB_TASK_MODULES=bricksim_rl
```

Then `isaaclab__train task=Lego-AssembleBrick-PPO-v0 rl_library=rsl_rl num_envs=32`.

The task forces CPU physics, so it is far slower than the GPU examples. Measured: 32 environments, 300 iterations in 1725 s at about 270 steps/s. Mean reward rose from 1.3 to 35, and the grasp reward has started to appear, but **no episode succeeded**: this confirms the training route, not a trained policy. The settings are a first guess. It has a long chain of stages, and its large reward comes only at the end, so expect to need much longer runs, or to shape the task. Play and eval were not run on it.
