"""A Franka with a push rod pushes one block out of a small tower: the rod's tip follows the policy through damped inverse kinematics."""

from __future__ import annotations

from collections.abc import Sequence

import torch

from isaaclab.controllers import DifferentialIKController, DifferentialIKControllerCfg
from isaaclab.envs import DirectRLEnv
from isaaclab.utils.math import quat_apply

from . import layout
from .env_cfg import JengaPushEnvCfg


class JengaPushEnv(DirectRLEnv):
    cfg: JengaPushEnvCfg

    def __init__(self, cfg: JengaPushEnvCfg, render_mode: str | None = None, **kwargs):
        super().__init__(cfg, render_mode, **kwargs)
        self._robot, self._tower = self.scene["robot"], self.scene["tower"]
        self._arm_ids = self._robot.find_joints("panda_joint.*")[0]
        self._finger_ids = self._robot.find_joints("panda_finger_joint.*")[0]
        self._hand_idx = self._robot.find_bodies(layout.HAND_LINK)[0][0]
        self._jacobian_idx = self._hand_idx - 1  # a fixed base has no Jacobian row of its own
        self._target_idx = self._tower.find_bodies(layout.TARGET)[0][0]
        self._top_idx = self._tower.find_bodies(layout.block_name(layout.LAYERS - 1, 1))[0][0]
        self._others = torch.tensor([i for i in range(self._tower.num_bodies) if i != self._target_idx], device=self.device)
        self._ik = DifferentialIKController(DifferentialIKControllerCfg(command_type="pose", use_relative_mode=False, ik_method="dls"), num_envs=self.num_envs, device=self.device)
        self._rod_offset = torch.tensor((0.0, 0.0, layout.ROD_TIP), device=self.device).repeat(self.num_envs, 1)
        self._hand_level = torch.tensor(layout.HAND_LEVEL_XYZW, device=self.device).repeat(self.num_envs, 1)
        self._level_offset = quat_apply(self._hand_level, self._rod_offset)
        self._tip_min = torch.tensor(layout.TIP_MIN, device=self.device)
        self._tip_max = torch.tensor(layout.TIP_MAX, device=self.device)
        self._tip_target = torch.tensor(layout.TIP_START, device=self.device).repeat(self.num_envs, 1)  # in the environment's frame
        self._previous_actions = torch.zeros_like(self.actions)
        self._finger_targets = torch.full((self.num_envs, len(self._finger_ids)), layout.READY_JOINTS["panda_finger_joint.*"], device=self.device)
        self._start_poses = self._tower.data.default_body_pose.torch.clone()  # the environment's frame
        self._fell = torch.zeros(self.num_envs, dtype=torch.bool, device=self.device)
        self._succeeded = torch.zeros(self.num_envs, dtype=torch.bool, device=self.device)
        self._best_push = torch.zeros(self.num_envs, device=self.device)
        self._worst_other = torch.zeros(self.num_envs, device=self.device)
        self._sums = {name: torch.zeros(self.num_envs, device=self.device) for name in ("reach", "align", "push", "disturb", "success", "action_rate", "fell")}

    # ------------------------------------------------------------ measurements

    def _block_positions(self) -> torch.Tensor:
        """Every block's position in its environment's frame, (envs, blocks, 3)."""
        return self._tower.data.body_pos_w.torch - self.scene.env_origins.unsqueeze(1)

    def _tip(self) -> torch.Tensor:
        """The rod's tip in the environment's frame."""
        pos = self._robot.data.body_pos_w.torch[:, self._hand_idx] - self.scene.env_origins
        return pos + quat_apply(self._robot.data.body_quat_w.torch[:, self._hand_idx], self._rod_offset)

    def _moved(self) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """How far the block has been pushed along x, how far the worst other block has moved, and every block's displacement."""
        displacement = self._block_positions() - self._start_poses[..., :3]
        pushed = displacement[:, self._target_idx, 0]
        worst = torch.linalg.norm(displacement[:, self._others], dim=-1).amax(dim=-1)
        return pushed, worst, displacement

    # ------------------------------------------------------------ one step

    def _pre_physics_step(self, actions: torch.Tensor) -> None:
        self._previous_actions[:] = self.actions
        self.actions[:] = actions.clamp(-1.0, 1.0)
        self._tip_target = torch.maximum(torch.minimum(self._tip_target + self.cfg.tip_step_m * self.actions, self._tip_max), self._tip_min)

    def _apply_action(self) -> None:
        hand_target = self._tip_target - self._level_offset  # the base stands at the environment's origin, unrotated
        self._ik.set_command(torch.cat((hand_target, self._hand_level), dim=-1))
        hand_pos = self._robot.data.body_pos_w.torch[:, self._hand_idx] - self.scene.env_origins
        hand_quat = self._robot.data.body_quat_w.torch[:, self._hand_idx]
        jacobian = self._robot.data.body_link_jacobian_w.torch[:, self._jacobian_idx, :, self._arm_ids]
        joints = self._ik.compute(hand_pos, hand_quat, jacobian, self._robot.data.joint_pos.torch[:, self._arm_ids])
        limits = self._robot.data.soft_joint_pos_limits.torch[:, self._arm_ids]
        target = self._robot.actuators.target_command
        target.set_position_index(value=torch.maximum(torch.minimum(joints, limits[..., 1]), limits[..., 0]), joint_ids=self._arm_ids)
        target.set_position_index(value=self._finger_targets, joint_ids=self._finger_ids)

    def _get_observations(self) -> dict[str, torch.Tensor]:
        pushed, worst, displacement = self._moved()
        block = self._block_positions()[:, self._target_idx]
        face = block - torch.tensor((layout.BLOCK[0] / 2, 0.0, 0.0), device=self.device)
        tip = self._tip()
        observation = torch.cat(
            (tip - face, self._tip_target - tip, displacement[:, self._target_idx], worst.unsqueeze(-1), displacement[:, self._top_idx], self.actions),
            dim=-1,
        )
        return {"policy": torch.nan_to_num(observation, nan=0.0, posinf=1.0, neginf=-1.0)}

    def _get_rewards(self) -> torch.Tensor:
        pushed, worst, _ = self._moved()
        block = self._block_positions()[:, self._target_idx]
        face = block - torch.tensor((layout.BLOCK[0] / 2, 0.0, 0.0), device=self.device)
        tip = self._tip()
        success = (pushed >= self.cfg.target_push_m) & (worst < self.cfg.still_m)
        fell = worst > self.cfg.fell_m
        self._fell |= fell
        self._succeeded |= success
        self._best_push = torch.maximum(self._best_push, pushed)
        self._worst_other = torch.maximum(self._worst_other, worst)
        terms = {
            "reach": self.cfg.reach_scale * (1.0 - torch.tanh(torch.linalg.norm(tip - face, dim=-1) / 0.05)),
            "align": self.cfg.align_scale * (1.0 - torch.tanh(torch.linalg.norm((tip - block)[:, 1:], dim=-1) / 0.01)),
            "push": self.cfg.push_scale * pushed.clamp(0.0, self.cfg.target_push_m) / self.cfg.target_push_m,
            "disturb": self.cfg.disturb_scale * (worst / self.cfg.still_m).clamp(0.0, 4.0) ** 2,
            "success": self.cfg.success_scale * success.float(),
            "action_rate": self.cfg.action_rate_scale * torch.sum((self.actions - self._previous_actions) ** 2, dim=-1),
        }
        reward = torch.zeros(self.num_envs, device=self.device)
        for name, value in terms.items():
            value = value * self.step_dt
            reward += value
            self._sums[name] += value
        once = self.cfg.fell_penalty * fell.float()  # once: the episode ends here
        self._sums["fell"] += once
        return reward + once

    def _get_dones(self) -> tuple[torch.Tensor, torch.Tensor]:
        _, worst, _ = self._moved()
        return worst > self.cfg.fell_m, self.episode_length_buf >= self.max_episode_length - 1

    def _reset_idx(self, env_ids: Sequence[int] | None) -> None:
        if env_ids is None:
            env_ids = self._robot._ALL_INDICES
        env_ids = torch.as_tensor(env_ids, device=self.device, dtype=torch.long)
        log = self.extras.setdefault("log", {})
        log["Metrics/success_rate"] = self._succeeded[env_ids].float().mean()
        log["Metrics/block_pushed_m"] = self._best_push[env_ids].mean()
        log["Metrics/worst_other_block_m"] = self._worst_other[env_ids].mean()
        log["Episode_Termination/fell"] = self._fell[env_ids].float().mean()
        log["Episode_Termination/time_out"] = 1.0 - self._fell[env_ids].float().mean()
        for name, total in self._sums.items():
            log[f"Episode_Reward/{name}"] = total[env_ids].mean() / self.max_episode_length_s
            total[env_ids] = 0.0

        super()._reset_idx(env_ids)

        positions = self._robot.data.default_joint_pos.torch[env_ids].clone()
        self._robot.write_joint_position_to_sim_index(position=positions, env_ids=env_ids)
        self._robot.write_joint_velocity_to_sim_index(velocity=torch.zeros_like(positions), env_ids=env_ids)
        poses = self._start_poses[env_ids].clone()
        poses[..., :3] += self.scene.env_origins[env_ids].unsqueeze(1)
        self._tower.write_body_pose_to_sim_index(body_poses=poses, env_ids=env_ids)
        self._tower.write_body_velocity_to_sim_index(body_velocities=torch.zeros((*poses.shape[:2], 6), device=self.device), env_ids=env_ids)
        self._ik.reset(env_ids)
        start = torch.tensor(layout.TIP_START, device=self.device).repeat(len(env_ids), 1)
        ny, nz = self.cfg.tip_noise_m
        start[:, 1] += (torch.rand(len(env_ids), device=self.device) * 2 - 1) * ny
        start[:, 2] += (torch.rand(len(env_ids), device=self.device) * 2 - 1) * nz
        self._tip_target[env_ids] = start
        self.actions[env_ids] = 0.0
        self._previous_actions[env_ids] = 0.0
        self._fell[env_ids] = False
        self._succeeded[env_ids] = False
        self._best_push[env_ids] = 0.0
        self._worst_other[env_ids] = 0.0
