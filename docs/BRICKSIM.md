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

The task forces CPU physics, so it is far slower than the GPU examples. Measured with 32 environments, at about 270 steps/s: 300 iterations took 29 minutes, then a resumed run took it to about 1,900 iterations in 2 hours more (it stopped there because its watcher was stopped, not because of the task; the tools reported it as failed, with its checkpoint saved). Mean reward rose from 1.3 to about 39. In eval over 32 episodes: **no success**, a mean return of 37, 6.6% dropped the brick, and nearly all of the return is from reaching (3.02 of the reward per second, grasping 0.08, lifting 0, inserting 0).

So the route works, and PPO with these settings has learned to reach and to grasp a little, not to lift or insert. This is a first guess at settings. The task's chain of stages has its large reward only at the end, so expect to need a shaped curriculum, a demonstration, or far more samples than CPU physics gives overnight; `bricksim/envs/assemble_brick/expert.py` is a scripted expert that could supply demonstrations. Playback was not run on it.
