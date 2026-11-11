"""Arm tools: the kinematic model of an articulation, inverse and forward kinematics."""

from pxr import UsdGeom, UsdPhysics
from isaac_mcp.project_assets import AssetError, refuse
from .core import vector
from .values import scalar
from .joint_tools import articulation_joints


class ArmTools:
    """Mixed into Advanced: uses its scene (``self.s``), sensors and ``physics_manager``."""

    def arm_model(self, stage, a, need_state):
        """The joint chain behind sim.arm.ik and sim.arm.fk: from the articulation's own USD joints, with the current
        joint values when the simulation can report them (always, if need_state)."""
        import numpy as np

        from . import ik
        from .physics import body_state

        s = self.s
        root = a["path"]
        try:
            state = self.joints(stage, root)  # needs an initialized articulation, so a playing or paused timeline
        except AssetError as exc:
            # Only "the simulation is not running" lets forward kinematics fall back to the authored pose; an invalid or
            # quarantined state must still reach the caller.
            if need_state or getattr(exc, "code", None) != "PHYSICS_NOT_READY":
                raise
            state = None
        root_prim = s.prim(stage, root)
        joint_prims, truncated = articulation_joints(stage, root_prim, 400)
        bodies = sorted({b for j in joint_prims for b in ik._bodies(j) if b})

        cut = (" The scan of this articulation's joints stopped at its limit, so the link may lie beyond it: "
               "give base_link nearer the end effector.") if truncated else ""

        def body(value, label):
            if not isinstance(value, str) or not value:
                refuse("INVALID_VALUE", f"{label} must be a link name or absolute path.")
            if value.startswith("/"):
                if value not in bodies:
                    refuse("NOT_FOUND", f"{value} is not a body of this articulation.{cut}", bodies=bodies[:20])
                return value
            found = [b for b in bodies if b.rsplit("/", 1)[-1] == value]
            if len(found) != 1:
                refuse("NOT_FOUND" if not found else "AMBIGUOUS",
                       f"{label} {value!r} matches {len(found)} bodies of this articulation; give the absolute path.{cut}",
                       candidates=(found or bodies)[:20])
            return found[0]

        ee = body(a["link"], "link")
        if a.get("base_link"):
            base = body(a["base_link"], "base_link")
        elif root_prim.HasAPI(UsdPhysics.RigidBodyAPI):
            base = root
        else:
            base = ik.find_base(joint_prims)
            if base is None:
                refuse("IK_NO_BASE", "Cannot tell which body is the base of this articulation; give base_link.", bodies=bodies[:20])
        mpu = UsdGeom.GetStageMetersPerUnit(stage) or 1.0
        now_time = s.timeline.get_current_time()
        notes = []
        base_pose = None
        if state is not None:
            try:
                r = body_state(base, now_time, False, stage)
                base_pose = ik.pose_matrix(r["world_position"], ik.quat_to_matrix(*r["orientation_wxyz"]))
            except Exception:  # a fixed base the engine does not report: its authored pose is the pose
                notes.append("Base pose taken from the authored USD, not read from physics.")
        else:
            notes.append("The simulation is not running, so the base pose is the authored USD pose.")
        try:
            chain = ik.build_chain(stage, joint_prims, base, ee, base_pose)
        except AssetError as exc:
            if truncated and getattr(exc, "code", None) == "IK_NO_CHAIN":
                refuse("IK_NO_CHAIN", f"{exc} The articulation scan stopped at its limit, so the chain may be cut off: "
                       "name a base_link nearer the end effector.", **getattr(exc, "details", {}))
            raise
        if not chain.names:
            refuse("IK_NO_JOINTS", f"No movable joints between {base} and {ee}.")
        if len(set(chain.names)) != len(chain.names):
            dupes = sorted({n for n in chain.names if chain.names.count(n) > 1})
            refuse("IK_DUPLICATE_JOINT", f"Joints in this chain share a name {dupes}, so their values cannot be told apart "
                   "by name. Rename them in the source, or pick a shorter chain with base_link.", joints=chain.names)
        chain.length_scale = 1.0 / mpu
        q_now = None
        if state is not None:
            dof = {n: i for i, n in enumerate(state["names"])}
            missing = [n for n in chain.names if n not in dof]
            if missing:
                refuse("IK_DOF_MISSING", f"The simulation has no degree of freedom named {missing}; its names are {state['names']}.")
            q_now = np.array([state["positions"][dof[n]] for n in chain.names], dtype=float)
        check = {"status": "skipped"}
        if q_now is not None:  # the model must put the end effector where physics says it is right now
            predicted = chain.fk(q_now)
            actual = None
            try:
                actual = body_state(ee, now_time, False, stage)
            except AssetError as exc:
                notes.append(f"Self-check skipped: physics reports no body at {ee} ({getattr(exc, 'code', 'error')}).")
            except Exception as exc:  # the link is not a rigid body physics can report
                notes.append(f"Self-check skipped: {str(exc)[:120]}")
            if actual is not None:
                ax = ik.pose_matrix(actual["world_position"], ik.quat_to_matrix(*actual["orientation_wxyz"]))
                pos_err = float(np.linalg.norm(ax[:3, 3] - predicted[:3, 3]))
                rot_err = float(np.linalg.norm(ik.rotation_vector(ax[:3, :3] @ predicted[:3, :3].T)))
                check = dict(status="ok", position_error=pos_err, orientation_error=rot_err)
                if pos_err > 0.02 / mpu or rot_err > 0.05:
                    refuse("IK_MODEL_MISMATCH", f"The joint model puts {ee} {pos_err * mpu:.3f} m and {rot_err:.3f} rad from where "
                           "physics reports it, so its answers would not be trusted. The usual causes are a joint frame the model "
                           "reads differently (a scaled body, a reversed joint, a mimic joint on the chain) or a robot still "
                           "settling: pause, then retry.", check=check, chain=chain.names)
                if float(np.max(np.abs(q_now))) < 1e-3:
                    notes.append("The self-check was made with every joint at zero, a pose that cannot reveal a flipped joint "
                                 "axis or sign. After a first small move, sim__arm__fk with the current joints is a better check.")
        offset = a.get("offset")
        if offset is not None:
            chain.set_tip(vector(offset, 3))
        return dict(chain=chain, root=root, base=base, ee=ee, mpu=mpu, q_now=q_now, check=check, notes=notes,
                    offset=list(vector(offset, 3)) if offset is not None else None)

    def arm_ik(self, stage, a):
        """Joint values that put a link at a target pose, from the articulation's own USD joints (solve only)."""
        import numpy as np

        from . import ik

        m = self.arm_model(stage, a, need_state=True)
        chain, root, base, ee, mpu, q_now = m["chain"], m["root"], m["base"], m["ee"], m["mpu"], m["q_now"]
        position = vector(a["position"], 3)
        use_orientation = a.get("orientation") is not None
        rotation = ik.quat_to_matrix(*vector(a["orientation"], 4)) if use_orientation else np.eye(3)
        target = ik.pose_matrix(position, rotation)
        frame = a.get("frame", "world")
        if frame not in ("world", "base"):
            refuse("INVALID_FRAME", "frame must be world or base.")
        if frame == "base":
            target = chain.base_pose @ target
        pos_tol = scalar(a.get("position_tolerance", 0.001), 1e-6, 1.0) / mpu
        rot_tol = scalar(a.get("orientation_tolerance", 0.01), 1e-4, 1.0)
        out = ik.solve(chain, q_now, target, use_orientation, pos_tol, rot_tol)
        targets = dict(zip(chain.names, [float(v) for v in out["q"]]))
        solved = chain.fk(out["q"])
        result = dict(
            reached=out["reached"],
            joint_targets=targets,
            position_error=out["position_error"],
            orientation_error=out["orientation_error"] if use_orientation else None,
            end_effector_pose=dict(position=solved[:3, 3].tolist(), orientation_wxyz=ik.matrix_to_quat(solved[:3, :3])),
            from_pose=dict(joints=dict(zip(chain.names, [float(v) for v in q_now]))),
            chain=dict(base=base, end_effector=ee, joints=chain.names),
            iterations=out["iterations"],
            attempts=out["attempts"],
            **({"timed_out": True} if out.get("timed_out") else {}),
            check=m["check"],
            units="radians for revolute joints, stage units for prismatic; errors in stage units and radians",
            next=(f"Move there: sim__joints__move with path={root!r} and targets={targets!r}. Solve only: nothing was commanded, "
                  "and there is no collision check or path between the poses.") if out["reached"] else
                 ("The target was not reached within tolerance: it may be outside the workspace or joint limits, or need a "
                  "different orientation. Try a nearer point, drop orientation, or loosen position_tolerance."),
        )
        if m["offset"] is not None:
            result["offset"] = m["offset"]
        if not out["reached"]:
            why = []
            inert = chain.inert_joints(q_now)
            if inert:
                why.append(f"At the current pose {inert} do not move the controlled point (their axes pass through it); if you meant "
                           "a tip or fingertip beyond the link's origin, pass offset, or name a link whose origin is the tip.")
            if use_orientation and len(chain.names) < 6:
                why.append(f"The chain has {len(chain.names)} movable joints, too few to hold an arbitrary orientation: place by position only, "
                           "or ask for an orientation the arm can reach.")
            if why:
                result["why_not_reached"] = why
        if m["notes"]:
            result["notes"] = m["notes"]
        return result

    def arm_fk(self, stage, a):
        """Where a link would be for given joint values (forward kinematics); commands nothing."""
        import numpy as np

        from . import ik

        m = self.arm_model(stage, a, need_state=False)
        chain, q_now = m["chain"], m["q_now"]
        given = a.get("joints")
        if given is not None and (not isinstance(given, dict) or not given):
            refuse("INVALID_VALUE", "joints must be an object of {joint name: value}, such as the joint_targets sim__arm__ik returns.")
        given = given or {}
        unknown = sorted(set(given) - set(chain.names))
        if unknown:
            refuse("NOT_FOUND", f"{unknown} are not movable joints of this chain; they are {chain.names}.", joints=chain.names)
        q = np.array(q_now if q_now is not None else np.zeros(len(chain.names)), dtype=float)
        source = {n: ("given" if n in given else ("current" if q_now is not None else "zero")) for n in chain.names}
        for i, n in enumerate(chain.names):
            if n in given:
                q[i] = scalar(given[n], -1e6, 1e6)
        outside = [n for i, n in enumerate(chain.names)
                   if not (chain.lower[i] - 1e-9 <= q[i] <= chain.upper[i] + 1e-9)]
        pose = chain.fk(q)
        relative = np.linalg.inv(chain.base_pose) @ pose
        result = dict(
            end_effector_pose=dict(position=pose[:3, 3].tolist(), orientation_wxyz=ik.matrix_to_quat(pose[:3, :3])),
            in_base_frame=dict(position=relative[:3, 3].tolist(), orientation_wxyz=ik.matrix_to_quat(relative[:3, :3])),
            joints=dict(zip(chain.names, [float(v) for v in q])),
            joint_source=source,
            chain=dict(base=m["base"], end_effector=m["ee"], joints=chain.names),
            **({"offset": m["offset"]} if m["offset"] is not None else {}),
            check=m["check"],
            units="radians for revolute joints, stage units for prismatic",
            note="Computed from the joint model; nothing was commanded.",
        )
        if outside:
            result["warning"] = f"Outside the authored joint limits: {outside}."
        if m["notes"]:
            result["notes"] = m["notes"]
        return result

    def tool_arm_ik(self, name, stage, a, camera_frame=None):
        if a.pop("speed", None) is not None:  # arm_path removes it for each point: a speed reaching here would be ignored
            refuse("INVALID_VALUE", "speed is not available on this route: call the tool itself.")
        move = a.pop("move", False)
        if type(move) is not bool:
            refuse("INVALID_VALUE", "move must be true or false.")
        solved = self.arm_ik(stage, a)
        if move and not solved["reached"]:  # a failed solve can be a very different pose: swinging there is never what was asked
            solved["commanded"] = None
            solved["next"] = "Nothing was commanded, because the target was not reached. " + solved["next"]
        elif move:  # solve, then command what was solved: one call for an arm to go somewhere
            solved["commanded"] = self.execute("sim.joints.command", stage, dict(path=a["path"], targets=solved["joint_targets"], clamp=True))
        return solved

    def tool_arm_fk(self, name, stage, a, camera_frame=None):
        return self.arm_fk(stage, a)
