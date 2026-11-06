"""Scene, simulation and PPO settings for ``Isaac-Urdf-Reach`` (Isaac Lab 3.0.0, PhysX)."""

from __future__ import annotations

from isaaclab_physx.physics import PhysxCfg
from isaaclab_physx.sim.schemas import PhysxArticulationCfg, PhysxRigidBodyCfg
from isaaclab_rl.rsl_rl import RslRlMLPModelCfg, RslRlOnPolicyRunnerCfg, RslRlPpoAlgorithmCfg

import isaaclab.sim as sim_utils
from isaaclab.actuators import ImplicitActuatorCfg
from isaaclab.assets import ArticulationCfg, AssetBaseCfg
from isaaclab.envs import DirectRLEnvCfg
from isaaclab.scene import InteractiveSceneCfg
from isaaclab.sim import SimulationCfg
from isaaclab.utils import configclass
from isaaclab.visualizers import VisualizerCfg


@configclass
class UrdfReachSceneCfg(InteractiveSceneCfg):
    robot: ArticulationCfg = ArticulationCfg(
        prim_path="{ENV_REGEX_NS}/Robot",
        spawn=sim_utils.UrdfFileCfg(
            asset_path="",  # set from env.urdf_path before the scene is built
            fix_base=False,  # the importer's fixed joint does not survive cloning: fix_root_link pins the base in each environment instead
            fix_root_link=True,
            merge_fixed_joints=False,  # the link to steer may hang from a fixed joint
            joint_drive=sim_utils.UrdfConverterCfg.JointDriveCfg(
                gains=sim_utils.UrdfConverterCfg.JointDriveCfg.PDGainsCfg(stiffness=None, damping=None),
            ),
            rigid_props=PhysxRigidBodyCfg(disable_gravity=True, max_depenetration_velocity=5.0),
            articulation_props=PhysxArticulationCfg(enabled_self_collisions=False, solver_position_iteration_count=8, solver_velocity_iteration_count=0),
            activate_contact_sensors=False,
        ),
        init_state=ArticulationCfg.InitialStateCfg(joint_pos={".*": 0.0}),  # set from the URDF's limits before the scene is built
        actuators={"all": ImplicitActuatorCfg(joint_names_expr=[".*"], stiffness=100.0, damping=10.0)},
    )
    plane = AssetBaseCfg(prim_path="/World/GroundPlane", spawn=sim_utils.GroundPlaneCfg())
    light = AssetBaseCfg(prim_path="/World/light", spawn=sim_utils.DomeLightCfg(color=(0.75, 0.75, 0.75), intensity=3000.0))


@configclass
class UrdfReachEnvCfg(DirectRLEnvCfg):
    urdf_path: str = ""
    end_link: str = ""
    stiffness: float = 100.0  # N m/rad, or N/m on a prismatic joint; a light arm wants less
    damping: float = 10.0

    episode_length_s = 6.0
    decimation = 2  # the policy acts at 60 Hz
    action_space = 0  # set from the URDF: one per driven joint
    observation_space = 0  # set from the URDF: two per driven joint, the target's offset, the last action
    state_space = 0
    sim: SimulationCfg = SimulationCfg(
        dt=1 / 120,
        render_interval=2,
        physics=PhysxCfg(),
        default_visualizer_cfg=VisualizerCfg(eye=(1.2, -1.2, 0.9), lookat=(0.0, 0.0, 0.2)),  # change with env.sim.default_visualizer_cfg.eye=[x,y,z]
    )
    scene: UrdfReachSceneCfg = UrdfReachSceneCfg(num_envs=1024, env_spacing=2.0, replicate_physics=True)

    action_scale = 0.5  # radians (or metres) from the start pose at a full action; targets are poses within this, so it also sets how far the arm has to go
    reachable_samples = 8  # how many random poses per environment are measured at startup as targets
    reached_m = 0.03
    reach_scale = 2.0
    action_rate_scale = -0.01
    joint_speed_scale = -0.0005


@configclass
class UrdfReachPPORunnerCfg(RslRlOnPolicyRunnerCfg):
    num_steps_per_env = 24
    max_iterations = 200
    save_interval = 50
    experiment_name = "urdf_reach"
    actor = RslRlMLPModelCfg(hidden_dims=[128, 64], activation="elu", obs_normalization=True,
                             distribution_cfg=RslRlMLPModelCfg.GaussianDistributionCfg(init_std=0.5))
    critic = RslRlMLPModelCfg(hidden_dims=[128, 64], activation="elu", obs_normalization=True)
    algorithm = RslRlPpoAlgorithmCfg(value_loss_coef=1.0, use_clipped_value_loss=True, clip_param=0.2, entropy_coef=1e-3, num_learning_epochs=5, num_mini_batches=4,
                                     learning_rate=1.0e-3, schedule="adaptive", gamma=0.99, lam=0.95, desired_kl=0.01, max_grad_norm=1.0)
