"""The scene, simulation and PPO settings for ``Isaac-Jenga-Push-Franka`` (Isaac Lab 3.0.0, PhysX)."""

from __future__ import annotations

from isaaclab_assets.robots.franka import FRANKA_PANDA_LEGACY_HIGH_PD_CFG
from isaaclab_physx.physics import PhysxCfg
from isaaclab_physx.sim.schemas import PhysxArticulationCfg, PhysxRigidBodyCfg
from isaaclab_rl.rsl_rl import RslRlMLPModelCfg, RslRlOnPolicyRunnerCfg, RslRlPpoAlgorithmCfg

import isaaclab.sim as sim_utils
from isaaclab.assets import ArticulationCfg, AssetBaseCfg, RigidObjectCfg, RigidObjectCollectionCfg
from isaaclab.envs import DirectRLEnvCfg
from isaaclab.scene import InteractiveSceneCfg
from isaaclab.sim import SimulationCfg
from isaaclab.utils import configclass, replace
from isaaclab.visualizers import VisualizerCfg

from . import layout

_WOOD = ((0.80, 0.65, 0.43), (0.76, 0.60, 0.38), (0.84, 0.70, 0.48))


def spawn_franka_with_rod(prim_path, cfg, translation=None, orientation=None, **kwargs):
    """The Franka, plus a thin box collider on its hand's rigid body: it moves with the hand and adds no joint. Runs before the environments are cloned."""
    from isaaclab.sim.spawners.from_files import spawn_from_usd
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    prim = spawn_from_usd(prim_path, cfg, translation, orientation, **kwargs)
    stage = prim.GetStage()
    hands = [p for p in Usd.PrimRange(prim) if p.GetName() == layout.HAND_LINK and p.HasAPI(UsdPhysics.RigidBodyAPI)]
    if len(hands) != 1:
        raise RuntimeError(f"Expected one {layout.HAND_LINK} rigid body under {prim.GetPath()}, found {len(hands)}.")
    rod = UsdGeom.Cube.Define(stage, hands[0].GetPath().AppendChild("PushRod"))
    rod.CreateSizeAttr(1.0)
    rod.CreateDisplayColorAttr([Gf.Vec3f(0.85, 0.2, 0.15)])
    xform = UsdGeom.Xformable(rod)
    xform.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, (layout.ROD_FROM + layout.ROD_TIP) / 2))
    xform.AddScaleOp().Set(Gf.Vec3f(layout.ROD_WIDTH, layout.ROD_WIDTH, layout.ROD_TIP - layout.ROD_FROM))
    UsdPhysics.CollisionAPI.Apply(rod.GetPrim())
    return prim


def _block_cfg(index, name, position, size) -> RigidObjectCfg:
    color = (0.8, 0.25, 0.2) if name == layout.TARGET else _WOOD[index % len(_WOOD)]
    return RigidObjectCfg(
        prim_path=f"{{ENV_REGEX_NS}}/Tower_{name}",
        init_state=RigidObjectCfg.InitialStateCfg(pos=position, rot=(0.0, 0.0, 0.0, 1.0)),
        spawn=sim_utils.CuboidCfg(
            size=size,
            rigid_props=PhysxRigidBodyCfg(solver_position_iteration_count=16, solver_velocity_iteration_count=1, max_depenetration_velocity=0.5),
            mass_props=sim_utils.MassPropertiesCfg(mass=layout.BLOCK_MASS),
            collision_props=sim_utils.CollisionPropertiesCfg(contact_offset=0.004, rest_offset=0.0),
            physics_material=sim_utils.RigidBodyMaterialCfg(static_friction=layout.FRICTION[0], dynamic_friction=layout.FRICTION[1], restitution=0.0),
            visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=color),
        ),
    )


def _tower_cfg() -> RigidObjectCollectionCfg:
    return RigidObjectCollectionCfg(rigid_objects={name: _block_cfg(i, name, pos, size) for i, (name, pos, size) in enumerate(layout.blocks())})


def _robot_cfg() -> ArticulationCfg:
    cfg = replace(FRANKA_PANDA_LEGACY_HIGH_PD_CFG, prim_path="{ENV_REGEX_NS}/Robot")
    # Self-collisions off, as in Lab's maintained Franka and in the quick-start scene.
    articulation = PhysxArticulationCfg(enabled_self_collisions=False, solver_position_iteration_count=8, solver_velocity_iteration_count=0)
    cfg.spawn = replace(cfg.spawn, func=spawn_franka_with_rod, articulation_props=articulation)
    cfg.init_state.joint_pos = dict(layout.READY_JOINTS)
    return cfg


@configclass
class JengaSceneCfg(InteractiveSceneCfg):
    robot: ArticulationCfg = _robot_cfg()
    tower: RigidObjectCollectionCfg = _tower_cfg()
    table = AssetBaseCfg(
        prim_path="{ENV_REGEX_NS}/Table",
        init_state=AssetBaseCfg.InitialStateCfg(pos=(layout.TABLE_CENTER_X, 0.0, layout.TABLE_TOP / 2)),
        spawn=sim_utils.CuboidCfg(
            size=(layout.TABLE_SIZE[0], layout.TABLE_SIZE[1], layout.TABLE_TOP),
            collision_props=sim_utils.CollisionPropertiesCfg(contact_offset=0.004, rest_offset=0.0),
            physics_material=sim_utils.RigidBodyMaterialCfg(static_friction=layout.FRICTION[0], dynamic_friction=layout.FRICTION[1], restitution=0.0),
            visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(0.3, 0.32, 0.36)),
        ),
    )
    plane = AssetBaseCfg(prim_path="/World/GroundPlane", spawn=sim_utils.GroundPlaneCfg())
    light = AssetBaseCfg(prim_path="/World/light", spawn=sim_utils.DomeLightCfg(color=(0.75, 0.75, 0.75), intensity=3000.0))


@configclass
class JengaPushEnvCfg(DirectRLEnvCfg):
    episode_length_s = 8.0
    decimation = 4  # the policy acts at 30 Hz
    action_space = 3  # how far to move the rod's tip this step, x y z
    observation_space = 16
    state_space = 0
    sim: SimulationCfg = SimulationCfg(
        dt=1 / 120,
        render_interval=4,
        physics=PhysxCfg(bounce_threshold_velocity=0.2, gpu_max_rigid_patch_count=2**20),
        default_visualizer_cfg=VisualizerCfg(eye=(1.7, -1.3, 1.15), lookat=(0.5, 0.0, 0.55)),  # one environment, seen from the side; change with env.sim.default_visualizer_cfg.eye=[x,y,z]
    )
    scene: JengaSceneCfg = JengaSceneCfg(num_envs=1024, env_spacing=2.5, replicate_physics=True)

    tip_step_m = 0.004  # a full action moves the tip target this far per policy step (12 cm/s)
    tip_noise_m = (0.005, 0.003)  # a new episode's start, y and z
    target_push_m = 0.10  # the block counts as out at this distance
    fell_m = 0.04  # another block moved this far: the tower has fallen, the episode ends
    still_m = 0.01  # success needs every other block within this

    reach_scale = 1.0
    align_scale = 1.0
    push_scale = 5.0
    disturb_scale = -0.5
    success_scale = 2.0
    action_rate_scale = -0.01
    fell_penalty = -10.0


@configclass
class JengaPushPPORunnerCfg(RslRlOnPolicyRunnerCfg):
    num_steps_per_env = 48
    max_iterations = 300
    save_interval = 50
    experiment_name = "jenga_push"
    actor = RslRlMLPModelCfg(hidden_dims=[128, 64], activation="elu", obs_normalization=True,
                             distribution_cfg=RslRlMLPModelCfg.GaussianDistributionCfg(init_std=0.5))
    critic = RslRlMLPModelCfg(hidden_dims=[128, 64], activation="elu", obs_normalization=True)
    algorithm = RslRlPpoAlgorithmCfg(value_loss_coef=1.0, use_clipped_value_loss=True, clip_param=0.2, entropy_coef=1e-3, num_learning_epochs=5, num_mini_batches=4,
                                     learning_rate=5.0e-4, schedule="adaptive", gamma=0.99, lam=0.95, desired_kl=0.02, max_grad_norm=1.0)
