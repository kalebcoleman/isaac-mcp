"""Train BrickSim's own assemble-brick task through the MCP tools, with PPO settings it does not ship. Isaac Lab 2.3.2 on Sim 5.1, CPU physics.

In the MCP server's environment: ``ISAACLAB_TASK_PATH=<repo>/examples:<BrickSim>/python``, ``ISAACLAB_TASK_MODULES=bricksim_rl``. Then
``isaaclab__train task=Lego-AssembleBrick-PPO-v0 rl_library=rsl_rl num_envs=32``. Nothing in BrickSim changes. See docs/BRICKSIM.md.
"""

import gymnasium as gym

gym.register(
    id="Lego-AssembleBrick-PPO-v0",
    entry_point="isaaclab.envs:ManagerBasedRLEnv",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": "bricksim.envs.assemble_brick.env:AssembleBrickEnvCfg",
        "rsl_rl_cfg_entry_point": f"{__name__}.ppo_cfg:AssembleBrickPPORunnerCfg",
    },
)
