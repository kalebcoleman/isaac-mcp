"""Your own fixed-base arm as an Isaac Lab 3.0.0 task: give a URDF and the link to steer, and a policy learns to reach random points with it.

Set ``ISAACLAB_TASK_PATH=<repo>/examples`` and ``ISAACLAB_TASK_MODULES=urdf_reach`` in the MCP server's environment, then::

    isaaclab__train task=Isaac-Urdf-Reach rl_library=rsl_rl extra_args=["env.urdf_path=/abs/robot.urdf", "env.end_link=gripper_link"]

Only this file is imported before Kit starts, so it must not import Isaac Lab. See docs/TOOLS-ISAACLAB.md.
"""

import gymnasium as gym

gym.register(
    id="Isaac-Urdf-Reach",
    entry_point=f"{__name__}.env:UrdfReachEnv",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": f"{__name__}.env_cfg:UrdfReachEnvCfg",
        "rsl_rl_cfg_entry_point": f"{__name__}.env_cfg:UrdfReachPPORunnerCfg",
        "default_agent": "rsl_rl",
    },
)
