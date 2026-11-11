"""sim.simulation.step: advance physics a bounded number of steps and report what happened."""

import math
from isaac_mcp.project_assets import AssetError, refuse
from .values import finite_clock


class StepTool:
    """Mixed into Advanced: uses its scene (``self.s``), sensors and ``physics_manager``."""

    def tool_simulation_step(self, name, stage, a, camera_frame=None):
        s = self.s
        if s.timeline.is_stopped() or s.timeline.is_playing():
            refuse(
                "PHYSICS_NOT_READY",
                "Pause an initialized simulation first: from a stopped timeline, sim__timeline__control action=play and then action=pause. Step does not initialize by playing or pumping frames.",
            )
        s.check_runtime_edit(stage)
        steps = a["steps"]
        bodies = a.get("observe_prims", [])
        arts = a.get("observe_joints", [])
        if (
            type(steps) is not int
            or not 1 <= steps <= 120
            or len(bodies) > 16
            or len(arts) > 8
        ):
            refuse("LIMIT", "steps 1..120, at most 16 bodies and 8 articulations.")
        target = dict(session_id=s.session_id, stage_id=s.stage_id)
        for p in bodies:
            self.dispatch("sim.body.state", dict(**target, path=p))
        for p in arts:
            self.joints(stage, p)
        sm = self.physics_manager()
        before = sm.get_simulation_time()
        dt = sm.get_physics_dt()
        if not finite_clock(before) or not finite_clock(dt) or dt <= 0:
            refuse(
                "PHYSICS_CLOCK_INVALID",
                "Runtime physics time and positive timestep must be finite; no steps requested.",
            )
        from .physics import step as backend_step, sync_fabric

        backend = sm.get_active_physics_engine()
        after = None
        fabric_updated = False
        phase = "native_step"
        try:
            backend_step(stage, steps)
            phase = "clock_observation"
            after = sm.get_simulation_time()
            if not finite_clock(after) or not math.isclose(
                after - before, steps * dt, rel_tol=1e-5, abs_tol=1e-7
            ):
                refuse(
                    "STEP_TIMING_MISMATCH",
                    "Observed physics time does not match the requested step interval; inspect state before retrying.",
                )
            phase = "fabric_update"
            fabric_updated = sync_fabric(stage)
            phase = "body_observation"
            body_states = [
                self.dispatch("sim.body.state", dict(**target, path=p))
                for p in bodies
            ]
            phase = "joint_observation"
            joint_states = [self.joints(stage, p) for p in arts]
        except Exception as exc:
            # A clock is evidence of elapsed simulation time, not proof
            # that the solver produced a valid pose or completed each step.
            if after is None:
                try:
                    after = sm.get_simulation_time()
                except Exception:
                    pass
            if not finite_clock(after):
                after = None
            details = dict(
                requested_steps=steps,
                physics_dt=dt,
                physics_time_before=before,
                physics_time_after=after,
                measured_delta=None if after is None else after - before,
                expected_delta=steps * dt,
                phase=phase,
                backend=backend,
                fabric_updated=fabric_updated,
                rendered=False,
            )
            effects = "Stepping was attempted and may have changed runtime state. No rollback or automatic retry was performed; inspect solver diagnostics before continuing."
            if isinstance(exc, AssetError):
                exc.details.update(step=details, effects=effects)
                raise
            message = str(exc)[:1500]
            if any(
                token in message.lower()
                for token in (
                    "cuda error 700",
                    "cudaerrorillegaladdress",
                    "illegal memory access",
                )
            ):
                s.device_error = message[:500]
                refuse(
                    "DEVICE_ERROR",
                    message,
                    step=details,
                    effects="GPU context may be unusable. Do not retry runtime/sensor operations; inspect/save/Stop, then restart the disposable Kit.",
                )
            refuse("STEP_FAILED", message, step=details, effects=effects)
        return dict(
            steps=steps,
            physics_dt=dt,
            physics_time_before=before,
            physics_time_after=after,
            measured_delta=after - before,
            expected_delta=steps * dt,
            timeline_time=s.timeline.get_current_time(),
            bodies=body_states,
            articulations=joint_states,
            backend=backend,
            fabric_updated=fabric_updated,
            rendered=False,
        )
