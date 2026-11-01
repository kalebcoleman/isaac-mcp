"""Dedicated typed scene operations. Native API adapters are version checked at use.

No app.update(), render(), World.step(), network fetch or arbitrary attribute setter.
"""

import hashlib
import math
import os
from pathlib import Path
import re
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, UsdShade
from isaac_mcp.project_assets import refuse
from .core import bounded, vector

MUTATIONS = {
    "sim.prim.delete",
    "sim.prim.clone",
    "sim.stage.clear_owned",
    "sim.light.create",
    "sim.light.modify",
    "sim.material.create",
    "sim.material.apply",
    "sim.physics.configure",
    "sim.joints.drive",
    "sim.camera.create",
    "sim.lidar.create",
    "sim.controller.create",
    "sim.controller.update",
}


def scalar(x, lo=0, hi=1e8):
    if type(x) not in (float, int) or not math.isfinite(x) or not lo <= x <= hi:
        refuse("INVALID_VALUE", f"Finite number in [{lo},{hi}] required.")
    return x


def color(value):
    vector(value, 3)
    return Gf.Vec3f(*[scalar(x, 0, 1) for x in value])


def owned(s, stage, p):
    prim = s.prim(stage, p)
    if p not in s.owned:
        refuse("NOT_OWNED", "Operation is limited to pack-created prims.")
    return prim


def deletable(s, stage, p, direct_sensor=False):
    prim = owned(s, stage, p)
    if (
        p == "/World"
        or stage.GetDefaultPrim()
        and stage.GetDefaultPrim().GetPath().HasPrefix(prim.GetPath())
    ):
        refuse("PROTECTED_PRIM", "World/default prim or its ancestor is protected.")
    nodes, truncated = bounded(stage, p, 200)
    if truncated:
        refuse("LIMIT", "Subtree deletion exceeds 200 prims.")
    if any(
        n.HasAPI(UsdPhysics.ArticulationRootAPI)
        or (
            not direct_sensor
            and (n.IsA(UsdGeom.Camera) or n.GetTypeName() == "OmniLidar")
        )
        or n.GetMetadata("no_delete")
        for n in nodes
    ):
        refuse(
            "PROTECTED_PRIM",
            "Subtree contains articulation/sensor/protected prim; use a new disposable session.",
        )
    return prim


def physics_manager():
    from .physics import manager

    return manager()


class Advanced:
    def __init__(self, scene, dispatch):
        self.s = scene
        self.dispatch = dispatch
        self.sensors = {}
        self.controllers = set()
        self.stage_id = None
        self.cleanup_errors = []

    def close_sensors(self, stage_closing=False):
        from .sensors import dispose

        for p, (_, sensor) in list(self.sensors.items()):
            self.cleanup_errors.extend(
                {"path": p, "error": e}
                for e in dispose(sensor, stage_closing=stage_closing)
            )
        self.cleanup_errors = self.cleanup_errors[-16:]
        self.sensors.clear()

    def invalidate(self):
        if self.stage_id != self.s.stage_id:
            self.close_sensors()
            self.controllers.clear()
            self.stage_id = self.s.stage_id

    def joints(self, stage, p):
        s = self.s
        prim = s.prim(stage, p)
        if s.timeline.is_stopped() or not prim.HasAPI(UsdPhysics.ArticulationRootAPI):
            refuse(
                "PHYSICS_NOT_READY",
                "Select an initialized articulation root on a playing/paused timeline.",
            )
        sm = physics_manager()
        from .physics import view_for

        art = view_for(p, articulation=True)

        def values(data):
            if hasattr(data, "numpy"):
                data = data.numpy()
            return data.tolist()[0]

        names = list(art.shared_metatype.dof_names)
        positions = values(art.get_dof_positions())
        velocities = values(art.get_dof_velocities())
        vector(positions, len(names))
        vector(velocities, len(names))
        return dict(
            path=p,
            positions=positions,
            velocities=velocities,
            names=names,
            source=sm.get_active_physics_engine() + " tensor view",
            backend=sm.get_active_physics_engine(),
            units="angular radians, prismatic stage units",
        )

    def import_urdf(self, stage, snapshot_id, manifest_sha256, prim_path, **pose):
        from isaac_mcp.project_assets import relative, signature

        s = self.s
        s.new_path(stage, prim_path)
        filename, manifest = s.broker.verify(snapshot_id, manifest_sha256, kind="urdf")
        data = filename.read_bytes()
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            refuse("URDF_XML", "DTDs and entities are not supported.")
        import xml.etree.ElementTree as ET

        tree = ET.fromstring(data)
        if tree.tag != "robot":
            refuse("URDF_XML", "Root must be robot.")
        for element in tree.iter():
            if "filename" in element.attrib:
                rel = relative(element.attrib["filename"])
                candidate = str(Path(manifest["entry"]).parent / rel)
                if candidate not in manifest["files"]:
                    refuse("DEPENDENCIES", "URDF file dependency missing: " + candidate)
        try:
            from isaacsim.asset.importer.urdf import URDFImporter, URDFImporterConfig
        except ImportError:
            refuse(
                "IMPORTER_UNAVAILABLE",
                "This Kit lacks the supported 6.x URDFImporter API.",
            )
        output = s.broker.staging / snapshot_id / "converted"
        config = URDFImporterConfig(urdf_path=str(filename), usd_path=str(output))
        root_before = stage.GetRootLayer().ExportToString()
        session_before = stage.GetSessionLayer().ExportToString()
        generated = Path(URDFImporter(config).import_urdf())
        if (
            s.get_stage() != stage
            or stage.GetRootLayer().ExportToString() != root_before
            or stage.GetSessionLayer().ExportToString() != session_before
        ):
            refuse(
                "IMPORTER_STAGE_CHANGED",
                "Native importer changed the active stage. Partial effects possible; inspect before retrying.",
            )
        if not generated.is_absolute():
            generated = output / generated
        # Generated artifacts are service-owned derivatives, not an approval of a
        # new model-supplied folder. Reuse the normal bounded snapshot pipeline.
        from isaac_mcp.project_assets import Broker, open_dir

        derived = Broker("")
        derived.staging = s.broker.staging
        fd = open_dir(output)
        try:
            ident = signature(os.fstat(fd))[:2]
        finally:
            os.close(fd)
        derived.roots = {"converted": (output, ident)}
        snap = derived.snapshot("converted", str(generated.relative_to(output)))
        result = s.import_asset(
            stage, snap["snapshot_id"], snap["manifest_sha256"], prim_path, **pose
        )
        result["urdf_snapshot_id"] = snapshot_id
        return result

    def execute(self, name, stage, a, *, camera_frame=None):
        s = self.s
        self.invalidate()
        if name == "sim.prim.delete":
            if a["confirm"] is not True:
                refuse("CONFIRM_REQUIRED", "confirm=true required.")
            p = a["path"]
            direct_sensor = p in self.sensors
            deletable(s, stage, p, direct_sensor=direct_sensor)
            if direct_sensor:
                from .sensors import dispose

                errors = dispose(self.sensors[p][1])
                if errors:
                    refuse(
                        "SENSOR_CLEANUP_FAILED",
                        "Some resources may have been released; inspect before retrying: "
                        + str(errors),
                    )
                del self.sensors[p]
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                if not stage.RemovePrim(p):
                    refuse("DELETE_FAILED", "Session-layer removal failed.")
            s.owned = {q for q in s.owned if q != p and not q.startswith(p + "/")}
            return dict(deleted=p, still_composed=bool(stage.GetPrimAtPath(p)))
        if name == "sim.stage.clear_owned":
            if a["confirm"] is not True:
                refuse("CONFIRM_REQUIRED", "confirm=true required.")
            removed = []
            refused = []
            for p in sorted(s.owned, key=len):
                if p not in s.owned:
                    continue
                try:
                    if p in self.sensors:
                        refuse(
                            "PROTECTED_PRIM",
                            "Clear preserves sensors; delete the exact owned sensor path explicitly.",
                        )
                    removed.append(
                        self.execute(
                            "sim.prim.delete", stage, dict(path=p, confirm=True)
                        )
                    )
                except ValueError as e:
                    refused.append(dict(path=p, reason=str(e)))
            return dict(
                removed=removed,
                refused=refused,
                scope="pack-owned only; current stage preserved",
            )
        if name == "sim.prim.clone":
            prim = owned(s, stage, a["path"])
            p, _ = s.new_path(stage, a["new_path"])
            if (
                prim.GetChildren()
                or prim.GetTypeName()
                not in ("Cube", "Sphere", "Cylinder", "Capsule", "Cone", "Xform")
                or prim.HasAuthoredReferences()
                or prim.HasAuthoredPayloads()
            ):
                refuse(
                    "UNSUPPORTED_CLONE",
                    "Only pack-created simple leaf geometry/Xform can be cloned.",
                )
            if not Sdf.CopySpec(
                stage.GetSessionLayer(), prim.GetPath(), stage.GetSessionLayer(), p
            ):
                refuse("CLONE_FAILED", "CopySpec returned false.")
            s.owned.add(str(p))
            return s.get(stage, str(p))
        if name.startswith("sim.light."):
            rgb = color(a.get("color", [1, 1, 1]))
            intensity = scalar(a.get("intensity", 1000))
            if name.endswith("create"):
                p, parent = s.new_path(stage, a["path"])
                kind = a.get("kind", "DistantLight")
                cls = {
                    "SphereLight": UsdLux.SphereLight,
                    "DistantLight": UsdLux.DistantLight,
                    "DomeLight": UsdLux.DomeLight,
                }.get(kind)
                if cls is None:
                    refuse(
                        "UNSUPPORTED_TYPE",
                        "Use SphereLight, DistantLight or DomeLight.",
                    )
                pose = s.pose(
                    stage,
                    parent,
                    a.get("position", [0, 0, 0]),
                    a.get("orientation", [1, 0, 0, 0]),
                    a.get("frame", "local"),
                )
                with Usd.EditContext(stage, stage.GetSessionLayer()):
                    prim = cls.Define(stage, p).GetPrim()
                    UsdGeom.Xformable(prim).AddTransformOp().Set(pose)
                s.owned.add(str(p))
            else:
                prim = owned(s, stage, a["path"])
            light = UsdLux.LightAPI(prim)
            if not light:
                refuse("WRONG_TYPE", "Not a USD light.")
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                light.CreateIntensityAttr(intensity)
                light.CreateColorAttr(rgb)
            return dict(
                path=str(prim.GetPath()),
                intensity=light.GetIntensityAttr().Get(),
                color=list(light.GetColorAttr().Get()),
            )
        if name == "sim.material.create":
            p, _ = s.new_path(stage, a["path"])
            rgb = color(a.get("color", [0.5, 0.5, 0.5]))
            rough = scalar(a.get("roughness", 0.5), 0, 1)
            metal = scalar(a.get("metallic", 0), 0, 1)
            kind = a.get("kind", "pbr")
            if kind not in ("pbr", "physics"):
                refuse("WRONG_TYPE", "Material kind must be pbr or physics.")
            friction = scalar(a.get("static_friction", 0.5), 0, 10)
            dynamic = scalar(a.get("dynamic_friction", 0.5), 0, 10)
            bounce = scalar(a.get("restitution", 0), 0, 1)
            if kind == "physics":
                with Usd.EditContext(stage, stage.GetSessionLayer()):
                    material = UsdShade.Material.Define(stage, p)
                    api = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
                    api.CreateStaticFrictionAttr(friction)
                    api.CreateDynamicFrictionAttr(dynamic)
                    api.CreateRestitutionAttr(bounce)
                s.owned.add(str(p))
                return dict(
                    path=str(p),
                    kind=kind,
                    static_friction=friction,
                    dynamic_friction=dynamic,
                    restitution=bounce,
                )
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                material = UsdShade.Material.Define(stage, p)
                shader = UsdShade.Shader.Define(stage, p.AppendChild("Surface"))
                shader.CreateIdAttr("UsdPreviewSurface")
                shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(rgb)
                shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(rough)
                shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(metal)
                material.CreateSurfaceOutput().ConnectToSource(
                    shader.ConnectableAPI(), "surface"
                )
            s.owned.add(str(p))
            return dict(
                path=str(p),
                shader="UsdPreviewSurface",
                color=list(rgb),
                roughness=rough,
                metallic=metal,
            )
        if name == "sim.material.apply":
            prim = s.prim(stage, a["path"])
            material = UsdShade.Material(owned(s, stage, a["material_path"]))
            if not material or not UsdGeom.Gprim(prim):
                refuse("WRONG_TYPE", "Need geometry and a pack-owned material.")
            purpose = (
                "physics" if material.GetPrim().HasAPI(UsdPhysics.MaterialAPI) else ""
            )
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                UsdShade.MaterialBindingAPI.Apply(prim).Bind(
                    material, materialPurpose=purpose
                )
            return dict(
                path=a["path"],
                material=str(
                    UsdShade.MaterialBindingAPI(prim)
                    .ComputeBoundMaterial(materialPurpose=purpose)[0]
                    .GetPath()
                ),
                purpose=purpose,
            )
        if name == "sim.physics.configure":
            sm = physics_manager()
            prim = s.prim(stage, a["path"])
            physics = UsdPhysics.Scene(prim)
            if not physics:
                refuse("WRONG_TYPE", "Not a PhysicsScene.")
            gravity = vector(a.get("gravity", [0, 0, -9.81]), 3)
            dt = scalar(a.get("dt", 1 / 60), 0.001, 0.1)
            length = math.sqrt(sum(x * x for x in gravity))
            direction = (
                Gf.Vec3f(*[x / length for x in gravity])
                if length
                else Gf.Vec3f(0, 0, -1)
            )
            from pxr import PhysxSchema

            with Usd.EditContext(stage, stage.GetSessionLayer()):
                physics.CreateGravityDirectionAttr(direction)
                physics.CreateGravityMagnitudeAttr(length)
                rate = round(1 / dt)
                if sm.get_active_physics_engine() == "physx":
                    PhysxSchema.PhysxSceneAPI.Apply(prim).CreateTimeStepsPerSecondAttr(
                        rate
                    )
                else:
                    if not prim.ApplyAPI("NewtonSceneAPI"):
                        refuse(
                            "PHYSICS_API_UNAVAILABLE",
                            "NewtonSceneAPI is unavailable; gravity may already have been authored.",
                        )
                    prim.GetAttribute("newton:timeStepsPerSecond").Set(rate)
                    solver_attr = prim.GetAttribute("newton:solver")
                    solver = solver_attr.Get() if solver_attr else None
                    if solver in (None, "", "mjcwarp", "mujoco"):
                        # Newton parses its outer rate from NewtonSceneAPI,
                        # while SimulationManager's MuJoCo wrapper reads this
                        # solver-specific timestep. Author the public schema:
                        # 6.0.0.0 ships the wrapper but does not export its class.
                        if not prim.ApplyAPI("MjcSceneAPI"):
                            refuse(
                                "PHYSICS_API_UNAVAILABLE",
                                "MjcSceneAPI unavailable; inspect partial scene configuration.",
                            )
                        attr = prim.GetAttribute("mjc:option:timestep")
                        if not attr or not attr.Set(1 / rate):
                            refuse(
                                "PHYSICS_API_UNAVAILABLE",
                                "MuJoCo timestep unavailable; inspect partial scene configuration.",
                            )
            return dict(path=a["path"], gravity=gravity, dt=1 / rate, requested_dt=dt)
        if name == "sim.joints.inspect":
            nodes, truncated = bounded(stage, a["path"], a.get("limit", 200))
            joints = []
            for prim in nodes:
                if prim.IsA(UsdPhysics.Joint):
                    attrs = {
                        key: prim.GetAttribute(key).Get()
                        for key in (
                            "physics:lowerLimit",
                            "physics:upperLimit",
                            "drive:angular:physics:targetPosition",
                            "drive:angular:physics:stiffness",
                            "drive:angular:physics:damping",
                        )
                        if prim.HasAttribute(key)
                    }
                    joints.append(
                        dict(
                            path=str(prim.GetPath()),
                            type=prim.GetTypeName(),
                            authored=attrs,
                        )
                    )
            return dict(
                joints=joints,
                truncated=truncated,
                units="USD angular degrees; prismatic stage units",
                source="authored USD, not DOF positions",
            )
        if name == "sim.joints.drive":
            prim = s.prim(stage, a["path"])
            angular = prim.IsA(UsdPhysics.RevoluteJoint)
            if not angular and not prim.IsA(UsdPhysics.PrismaticJoint):
                refuse("WRONG_TYPE", "Expected revolute/prismatic joint.")
            target = scalar(a["target"], -1e6, 1e6)
            stiff = scalar(a["stiffness"])
            damp = scalar(a["damping"])
            force = scalar(a.get("max_force", 1000), 1e-9)
            usd_target = math.degrees(target) if angular else target
            lo = prim.GetAttribute("physics:lowerLimit").Get()
            hi = prim.GetAttribute("physics:upperLimit").Get()
            if lo is not None and usd_target < lo or hi is not None and usd_target > hi:
                refuse("JOINT_LIMIT", "Requested target exceeds authored joint limits.")
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                drive = UsdPhysics.DriveAPI.Apply(
                    prim, "angular" if angular else "linear"
                )
                drive.CreateTargetPositionAttr(usd_target)
                drive.CreateStiffnessAttr(stiff)
                drive.CreateDampingAttr(damp)
                drive.CreateMaxForceAttr(force)
            return dict(
                path=a["path"],
                requested_target=target,
                authored_target=usd_target,
                unit="degrees" if angular else "stage units",
                effect="drive target authored; motion unverified until runtime observation",
            )
        if name == "sim.joints.command":
            state = self.joints(stage, a["path"])
            if not state["names"]:
                refuse("JOINT_COUNT", "Selected articulation has no commandable DOFs.")
            positions = vector(a["positions"], len(state["names"]))
            from .physics import view_for

            view = view_for(a["path"], articulation=True)
            limits = view.get_dof_limits()
            if hasattr(limits, "numpy"):
                limits = limits.numpy()
            if getattr(limits, "shape", None) != (1, len(positions), 2):
                refuse(
                    "PHYSICS_FORMAT",
                    "Runtime DOF limit shape does not match the articulation.",
                )
            for value, bounds in zip(positions, limits[0]):
                if any(math.isnan(float(b)) for b in bounds) or bounds[0] > bounds[1]:
                    refuse("PHYSICS_FORMAT", "Runtime DOF limits are invalid.")
                if value < float(bounds[0]) or value > float(bounds[1]):
                    refuse("JOINT_LIMIT", "Target exceeds runtime joint limits.")
            current = view.get_dof_position_targets()
            import numpy as np

            targets = np.array([positions], dtype=np.float32)
            indices = np.array([0], dtype=np.uint32)
            if hasattr(current, "device"):
                import warp as wp

                targets = wp.array(targets, device=current.device)
                indices = wp.array(indices, dtype=wp.uint32, device=current.device)
            view.set_dof_position_targets(targets, indices)
            return dict(
                path=a["path"],
                targets=positions,
                names=state["names"],
                achieved=False,
                warning="Active controllers may replace these targets; inspect runtime joints after stepping.",
            )
        if name == "sim.joints.state":
            return self.joints(stage, a["path"])
        if name == "sim.simulation.step":
            if s.timeline.is_stopped() or s.timeline.is_playing():
                refuse(
                    "PHYSICS_NOT_READY",
                    "Pause an initialized simulation first. Step does not initialize by playing or pumping frames.",
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
            sm = physics_manager()
            before = sm.get_simulation_time()
            dt = sm.get_physics_dt()
            from .physics import step as backend_step

            backend_step(stage, steps)
            after = sm.get_simulation_time()
            if not math.isclose(after - before, steps * dt, rel_tol=1e-5, abs_tol=1e-7):
                refuse(
                    "STEP_TIMING_MISMATCH",
                    f"Physics advanced {after - before}, expected {steps * dt}; state changed, inspect before retry.",
                )
            from .physics import sync_fabric

            fabric_updated = sync_fabric(stage)
            target = dict(session_id=s.session_id, stage_id=s.stage_id)
            return dict(
                steps=steps,
                physics_dt=dt,
                physics_time_before=before,
                physics_time_after=after,
                measured_delta=after - before,
                expected_delta=steps * dt,
                timeline_time=s.timeline.get_current_time(),
                bodies=[
                    self.dispatch("sim.body.state", dict(**target, path=p))
                    for p in bodies
                ],
                articulations=[self.joints(stage, p) for p in arts],
                backend=sm.get_active_physics_engine(),
                fabric_updated=fabric_updated,
                rendered=False,
            )
        if name in ("sim.camera.create", "sim.lidar.create"):
            p, parent = s.new_path(stage, a["path"])
            pose = s.pose(
                stage,
                parent,
                a.get("position", [0, 0, 0]),
                a.get("orientation", [1, 0, 0, 0]),
                a.get("frame", "local"),
            )
            if name == "sim.camera.create" and a.get("target") is not None:
                if a.get("frame", "local") != "world":
                    refuse("INVALID_FRAME", "Camera target requires frame=world.")
                eye = Gf.Vec3d(*vector(a.get("position", [0, 0, 0]), 3))
                target = Gf.Vec3d(*vector(a["target"], 3))
                up = (
                    Gf.Vec3d(0, 0, 1)
                    if UsdGeom.GetStageUpAxis(stage) == "Z"
                    else Gf.Vec3d(0, 1, 0)
                )
                direction = target - eye
                if (
                    direction.GetLength() < 1e-8
                    or Gf.Cross(direction, up).GetLength() < 1e-8
                ):
                    refuse(
                        "INVALID_POSE",
                        "Look-at needs distinct eye/target not parallel to stage up-axis.",
                    )
                pose = (
                    Gf.Matrix4d().SetLookAt(eye, target, up).GetInverse()
                    * UsdGeom.XformCache().GetLocalToWorldTransform(parent).GetInverse()
                )
            if len(self.sensors) >= 8:
                refuse(
                    "LIMIT",
                    "At most eight live sensors per session; delete an owned sensor to release its resources.",
                )
            if name == "sim.camera.create":
                w = a.get("width", 640)
                h = a.get("height", 480)
                if (
                    type(w) is not int
                    or type(h) is not int
                    or not 64 <= w <= 1024
                    or not 64 <= h <= 1024
                ):
                    refuse("LIMIT", "width/height must be integers 64..1024.")
            from .sensors import create as create_sensor

            kind = "camera" if name == "sim.camera.create" else "lidar"
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                sensor = create_sensor(
                    stage,
                    p,
                    kind,
                    w if kind == "camera" else 128,
                    h if kind == "camera" else 128,
                )
                xf = UsdGeom.Xformable(stage.GetPrimAtPath(p))
                xf.ClearXformOpOrder()
                xf.AddTransformOp().Set(pose)
            self.sensors[str(p)] = (kind, sensor)
            s.owned.add(str(p))
            return dict(
                path=str(p),
                kind=kind,
                ready=False,
                adapter=sensor["adapter"],
                release=sensor["release"],
                build=sensor["build"],
                note="Needs ordinary rendered warm-up; no implicit Play. Direct owned-sensor deletion releases render resources.",
            )
        if name in ("sim.camera.capture", "sim.lidar.read"):
            entry = self.sensors.get(a["path"])
            expected = "camera" if name == "sim.camera.capture" else "lidar"
            if entry is None or entry[0] != expected:
                refuse("NOT_OWNED", "Sensor is not registered in this session.")
            sensor = entry[1]
            prim = s.prim(stage, a["path"])
            if not prim.IsActive():
                refuse("SENSOR_INVALIDATED", "Sensor prim is inactive.")
            if expected == "lidar":
                limit = a.get("limit", 64)
                if type(limit) is not int or not 1 <= limit <= 256:
                    refuse("LIMIT", "limit must be 1..256.")
                attr = prim.GetAttribute("omni:sensor:Core:outputFrameOfReference")
                if not attr or attr.Get() != "SENSOR":
                    refuse(
                        "SENSOR_MODIFIED",
                        "LiDAR output frame changed outside this tool; restore SENSOR before reading.",
                    )
                from .sensors import lidar_points

                return dict(path=a["path"], **lidar_points(sensor, limit))
            h, w = sensor["resolution"]
            if not 64 <= h <= 1024 or not 64 <= w <= 1024:
                refuse(
                    "SENSOR_FORMAT",
                    "Camera resolution changed outside configured bounds.",
                )
            from .sensors import read as read_sensor

            data = camera_frame if camera_frame is not None else read_sensor(sensor)[0]
            if expected == "camera":
                import numpy as np
                from PIL import Image

                if (
                    not isinstance(data, np.ndarray)
                    or data.dtype != np.uint8
                    or data.shape not in ((h, w, 3), (h, w, 4))
                ):
                    refuse(
                        "SENSOR_FORMAT",
                        "Expected bounded CPU uint8 RGB/RGBA data; no GPU-pointer fallback.",
                    )
                array = np.array(data[:, :, :3], copy=True, order="C")
                filename = a["filename"]
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,59}\.png", filename):
                    refuse("INVALID_OUTPUT", "Simple new .png basename required.")
                s.output.mkdir(mode=0o700, parents=True, exist_ok=True)
                from isaac_mcp.project_assets import open_dir

                fd = open_dir(s.output)
                try:
                    # Exclusive descriptor creation prevents symlink/overwrite races.
                    out = os.open(
                        filename,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=fd,
                    )
                    with os.fdopen(out, "wb") as stream:
                        Image.fromarray(array).save(stream, format="PNG")
                finally:
                    os.close(fd)
                file = s.output / filename
                return dict(
                    file=str(file),
                    width=array.shape[1],
                    height=array.shape[0],
                    sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
                    source="Hydra renderer CPU callback (owned copy)"
                    if camera_frame is not None
                    else "Replicator CPU RGB annotator (owned copy)",
                    adapter="hydra-renderer-cpu-v1"
                    if camera_frame is not None
                    else sensor["adapter"],
                    freshness="new render and CPU transfer completed"
                    if camera_frame is not None
                    else "latest available render frame; can be stale",
                )
        if name == "sim.logs.tail":
            import carb.settings

            log = carb.settings.get_settings().get("/log/file")
            limit = a.get("limit", 50)
            if type(limit) is not int or not 1 <= limit <= 100:
                refuse("LIMIT", "limit 1..100 required.")
            if not log or not Path(log).is_file():
                refuse("LOG_UNAVAILABLE", "Kit did not expose an existing /log/file.")
            with open(log, "rb") as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 32768))
                lines = stream.read(32768).decode(errors="replace").splitlines()
            return dict(
                file=str(log),
                lines=[x[:512] for x in lines if "Warning" in x or "Error" in x][
                    -limit:
                ],
                scope="last 32 KiB; WARN/ERROR only",
            )
        if name.startswith("sim.controller."):
            if os.getenv("ISAAC_MCP_ENABLE_SCENE_SCRIPTS") != "1":
                refuse(
                    "SCRIPT_DISABLED",
                    "User must opt in at Kit launch with ISAAC_MCP_ENABLE_SCENE_SCRIPTS=1. This executes arbitrary code.",
                )
            code = a["code"]
            p = a["path"]
            if not isinstance(code, str) or len(code) > 16384:
                refuse("LIMIT", "Script is limited to 16384 characters.")
            compile(code, "<trusted MCP controller>", "exec")
            import omni.graph.core as og

            if name.endswith("create"):
                s.new_path(stage, p)
                with Usd.EditContext(stage, stage.GetSessionLayer()):
                    og.Controller.edit(
                        dict(graph_path=p, evaluator_name="execution"),
                        {
                            og.Controller.Keys.CREATE_NODES: [
                                ("Tick", "omni.graph.action.OnPlaybackTick"),
                                ("Script", "omni.graph.scriptnode.ScriptNode"),
                            ],
                            og.Controller.Keys.CONNECT: [
                                ("Tick.outputs:tick", "Script.inputs:execIn")
                            ],
                            og.Controller.Keys.SET_VALUES: [
                                ("Script.inputs:usePath", False),
                                ("Script.inputs:script", code),
                            ],
                        },
                    )
                self.controllers.add(p)
                s.owned.add(p)
            else:
                if p not in self.controllers:
                    refuse("NOT_OWNED", "Not a pack-created controller.")
                with Usd.EditContext(stage, stage.GetSessionLayer()):
                    og.Controller.set(
                        og.Controller.attribute(p + "/Script.inputs:script"), code
                    )
                node = og.Controller.node(p + "/Script")
                og.Controller.set(node.get_attribute("state:omni_initialized"), False)
            return dict(
                path=p,
                code_sha256=hashlib.sha256(code.encode()).hexdigest(),
                executes="on playback tick",
                sandboxed=False,
            )
        refuse("UNKNOWN_TOOL", "No handler for " + name)
