"""Reach random points with any fixed-base arm, given only its URDF and the link to steer."""

from __future__ import annotations

from collections.abc import Sequence

import torch

from isaaclab.envs import DirectRLEnv

from . import urdf_info
from .env_cfg import UrdfReachEnvCfg


class UrdfReachEnv(DirectRLEnv):
    cfg: UrdfReachEnvCfg

    def __init__(self, cfg: UrdfReachEnvCfg, render_mode: str | None = None, **kwargs):
        info = urdf_info.read(cfg.urdf_path, cfg.end_link)
        count = len(info["joints"])
        cfg.action_space, cfg.observation_space = count, 3 * count + 3 + 3
        cfg.scene.robot.spawn.asset_path = cfg.urdf_path
        cfg.scene.robot.init_state.joint_pos = urdf_info.start_pose(info)
        cfg.scene.robot.actuators["all"].joint_names_expr = info["joints"]
        cfg.scene.robot.actuators["all"].stiffness = cfg.stiffness
        cfg.scene.robot.actuators["all"].damping = cfg.damping
        super().__init__(cfg, render_mode, **kwargs)
        self._robot = self.scene["robot"]
        self._joint_ids = self._robot.find_joints(info["joints"], preserve_order=True)[0]
        self._link_idx = self._robot.find_bodies(cfg.end_link)[0][0]
        self._previous_actions = torch.zeros_like(self.actions)
        self._target = torch.zeros((self.num_envs, 3), device=self.device)
        self._home, self._reachable = self._measure_reachable()
        self._closest = torch.full((self.num_envs,), 9.0, device=self.device)
        self._final = torch.zeros(self.num_envs, device=self.device)
        self._sums = {name: torch.zeros(self.num_envs, device=self.device) for name in ("reach", "action_rate", "joint_speed")}

    def _measure_reachable(self) -> tuple[torch.Tensor, torch.Tensor]:
        """Where the link is at the start pose, and points it can really get to: it is put in random poses the policy's actions can reach, and
        where it ends up is read from physics. So a target suits any arm, with no workspace to guess. Runs once, before training."""
        start = self._robot.data.default_joint_pos.torch.clone()
        limits = self._robot.data.soft_joint_pos_limits.torch
        points = []
        for sample in range(1 + self.cfg.reachable_samples):
            joints = start.clone()
            if sample:  # the first pass, unchanged, is the start pose
                noise = (torch.rand_like(joints[:, self._joint_ids]) * 2 - 1) * self.cfg.action_scale
                joints[:, self._joint_ids] = torch.maximum(torch.minimum(joints[:, self._joint_ids] + noise, limits[:, self._joint_ids, 1]), limits[:, self._joint_ids, 0])
            self._robot.write_joint_position_to_sim_index(position=joints)
            self._robot.write_joint_velocity_to_sim_index(velocity=torch.zeros_like(joints))
            self.sim.step(render=False)
            self.scene.update(self.physics_dt)
            points.append(self._link_pos().clone())
        self._robot.write_joint_position_to_sim_index(position=start)
        self._robot.write_joint_velocity_to_sim_index(velocity=torch.zeros_like(start))
        return points[0], torch.cat(points[1:], dim=0)

    def _link_pos(self) -> torch.Tensor:
        return self._robot.data.body_pos_w.torch[:, self._link_idx] - self.scene.env_origins

    def _pre_physics_step(self, actions: torch.Tensor) -> None:
        self._previous_actions[:] = self.actions
        self.actions[:] = actions.clamp(-1.0, 1.0)

    def _apply_action(self) -> None:
        start = self._robot.data.default_joint_pos.torch[:, self._joint_ids]
        limits = self._robot.data.soft_joint_pos_limits.torch[:, self._joint_ids]
        wanted = start + self.cfg.action_scale * self.actions
        self._robot.actuators.target_command.set_position_index(value=torch.maximum(torch.minimum(wanted, limits[..., 1]), limits[..., 0]), joint_ids=self._joint_ids)

    def _get_observations(self) -> dict[str, torch.Tensor]:
        joints = self._robot.data.joint_pos.torch[:, self._joint_ids] - self._robot.data.default_joint_pos.torch[:, self._joint_ids]
        speeds = self._robot.data.joint_vel.torch[:, self._joint_ids]
        return {"policy": torch.cat((joints, speeds, self._target - self._link_pos(), self._target - self._home, self.actions), dim=-1)}

    def _get_rewards(self) -> torch.Tensor:
        distance = torch.linalg.norm(self._target - self._link_pos(), dim=-1)
        self._closest = torch.minimum(self._closest, distance)
        self._final[:] = distance
        terms = {
            "reach": self.cfg.reach_scale * (1.0 - torch.tanh(distance / 0.1)),
            "action_rate": self.cfg.action_rate_scale * torch.sum((self.actions - self._previous_actions) ** 2, dim=-1),
            "joint_speed": self.cfg.joint_speed_scale * torch.sum(self._robot.data.joint_vel.torch[:, self._joint_ids] ** 2, dim=-1),
        }
        reward = torch.zeros(self.num_envs, device=self.device)
        for name, value in terms.items():
            value = value * self.step_dt
            reward += value
            self._sums[name] += value
        return reward

    def _get_dones(self) -> tuple[torch.Tensor, torch.Tensor]:
        return torch.zeros(self.num_envs, dtype=torch.bool, device=self.device), self.episode_length_buf >= self.max_episode_length - 1

    def _reset_idx(self, env_ids: Sequence[int] | None) -> None:
        if env_ids is None:
            env_ids = self._robot._ALL_INDICES
        env_ids = torch.as_tensor(env_ids, device=self.device, dtype=torch.long)
        log = self.extras.setdefault("log", {})
        log["Metrics/final_distance_m"] = self._final[env_ids].mean()
        log["Metrics/closest_distance_m"] = self._closest[env_ids].mean()
        log["Metrics/reached_rate"] = (self._final[env_ids] < self.cfg.reached_m).float().mean()
        for name, total in self._sums.items():
            log[f"Episode_Reward/{name}"] = total[env_ids].mean() / self.max_episode_length_s
            total[env_ids] = 0.0
        super()._reset_idx(env_ids)
        positions = self._robot.data.default_joint_pos.torch[env_ids].clone()
        self._robot.write_joint_position_to_sim_index(position=positions, env_ids=env_ids)
        self._robot.write_joint_velocity_to_sim_index(velocity=torch.zeros_like(positions), env_ids=env_ids)
        picks = torch.randint(0, self._reachable.shape[0], (len(env_ids),), device=self.device)
        self._target[env_ids] = self._reachable[picks]
        self.actions[env_ids] = 0.0
        self._previous_actions[env_ids] = 0.0
        self._closest[env_ids] = 9.0
