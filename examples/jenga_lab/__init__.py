"""A Jenga task for Isaac Lab 3.0.0: a Franka with a push rod learns to push one block out of a small tower without toppling it.

Run through the MCP tools with, in the server's environment, ``ISAACLAB_TASK_PATH=<repo>/examples`` and
``ISAACLAB_TASK_MODULES=jenga_lab``, then ``isaaclab__train task=Isaac-Jenga-Push-Franka``. PhysX only. See docs/TOOLS-ISAACLAB.md.
Only this file is imported before Kit starts, so it must not import Isaac Lab.
"""

import gymnasium as gym

gym.register(
    id="Isaac-Jenga-Push-Franka",
    entry_point=f"{__name__}.env:JengaPushEnv",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": f"{__name__}.env_cfg:JengaPushEnvCfg",
        "rsl_rl_cfg_entry_point": f"{__name__}.env_cfg:JengaPushPPORunnerCfg",
        "default_agent": "rsl_rl",
    },
)
