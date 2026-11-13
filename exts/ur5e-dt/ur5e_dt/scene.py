"""Pack-backed workcell assembly, independent of the retired twin mixins."""

import copy
import math
from pathlib import Path

ROLES = ("robot", "bench", "base_plate", "bota", "quick_changer", "rg2", "finger_pad",
         "d415", "wrist_mount", "overhead_mount", "overhead_release", "overhead_shelf",
         "jenga_block", "materials")


def _preflight(config_path, mode):
    from . import asset_pack, hardware_config

    if mode not in ("physics", "mirror_real"):
        raise ValueError("mode must be physics or mirror_real")
    config, raw = hardware_config.load_config(config_path)
    roles = config.get("assets", {})
    if any(not isinstance(roles.get(role), str) or not roles[role] for role in ROLES):
        raise ValueError("assets must map every workcell role to a logical asset ID")
    world = config["world"]
    xy = world["workstation_xy_m"]
    scale = world["bench_scale"]
    top = world["bench_asset_top_z_m"]
    if (len(xy) != 2 or not all(math.isfinite(float(value)) for value in xy)
            or not math.isfinite(float(scale)) or scale <= 0
            or not math.isfinite(float(top)) or world["tabletop_z_m"] != 0):
        raise ValueError("invalid bench offset, scale, asset tabletop height, or tabletop_z_m")
    jenga = config["jenga"]
    if list(jenga["block_size_m"]) != [0.072, 0.024, 0.014]:
        raise ValueError("jenga block dimensions must be 72 x 24 x 14 mm")
    if type(jenga["tower_layers_built"]) is not int or not 1 <= jenga["tower_layers_built"] <= 18:
        raise ValueError("jenga tower_layers_built must be 1..18")
    if not math.isfinite(float(jenga["block_mass_kg"])) or jenga["block_mass_kg"] <= 0:
        raise ValueError("jenga block mass must be positive")
    if len(jenga["tower_xy_m"]) != 2 or not all(math.isfinite(float(value)) for value in jenga["tower_xy_m"]):
        raise ValueError("invalid jenga tower position")
    pack = asset_pack.resolve_pack(config)
    pack.verify_dependencies()
    assets = {}
    for role in ROLES:
        path = Path(pack.asset(roles[role]))
        if not path.is_absolute() or not path.is_file():
            raise ValueError(f"asset {role} must resolve to an existing absolute file")
        assets[role] = path
    return config, raw, pack, assets


def _tag_parts(stage, parts):
    from pxr import Sdf

    seen = set()
    for part in parts:
        path, asset_id, bom_id = (part[key] for key in ("prim_path", "asset_id", "bom_id"))
        if not all(isinstance(value, str) and value for value in (path, asset_id, bom_id)) or path in seen:
            raise ValueError(f"invalid or duplicate part mapping: {part!r}")
        seen.add(path)
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            raise ValueError(f"part prim does not exist: {path}")
        prim.CreateAttribute("workcell:asset_id", Sdf.ValueTypeNames.String, custom=True).Set(asset_id)
        prim.CreateAttribute("workcell:bom_id", Sdf.ValueTypeNames.String, custom=True).Set(bom_id)


class Workcell:
    def __init__(self):
        self._report = {"state": "idle"}

    def status(self):
        return copy.deepcopy(self._report)

    async def quick_start(self, config_path=None, mode="physics", discard_changes=False):
        if type(discard_changes) is not bool:
            raise ValueError("discard_changes must be a boolean")
        config, raw, pack, assets = _preflight(config_path, mode)
        from .physics_compat import selected_engine
        if selected_engine() != "physx":
            raise RuntimeError("Workcell requires the selected PhysX backend; no fallback was attempted")
        import omni.usd
        import omni.timeline
        from sim_tools.stage_open import stage_has_unsaved_work
        from pxr import Gf, UsdGeom, UsdLux, UsdPhysics
        from . import robot, cameras, jenga

        if not callable(getattr(robot, "build_robot", None)) or not callable(getattr(cameras, "build_cameras", None)):
            raise RuntimeError("workcell robot and camera builders are not installed")
        context = omni.usd.get_context()
        timeline = omni.timeline.get_timeline_interface()
        if not timeline.is_stopped():
            raise RuntimeError("Stop the timeline before replacing the stage")
        stage = context.get_stage()
        if stage and stage_has_unsaved_work(stage) and not discard_changes:
            raise RuntimeError("UNSAVED_CHANGES: pass discard_changes=True to replace the existing stage")
        ok, error = await context.new_stage_async()
        if not ok:
            raise RuntimeError(f"Could not create workcell stage: {error}")
        self._report = {"state": "building", "mode": mode}
        try:
            stage = context.get_stage()
            UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
            UsdGeom.SetStageMetersPerUnit(stage, 1.0)
            world = UsdGeom.Xform.Define(stage, "/World")
            stage.SetDefaultPrim(world.GetPrim())
            physics = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
            physics.CreateGravityDirectionAttr().Set(Gf.Vec3f(0, 0, -1))
            physics.CreateGravityMagnitudeAttr().Set(9.81 if mode == "physics" else 0.0)
            light = UsdLux.DistantLight.Define(stage, "/World/Light")
            light.CreateIntensityAttr().Set(3000.0)
            bench = UsdGeom.Xform.Define(stage, "/World/Bench")
            bench.GetPrim().GetReferences().AddReference(str(assets["bench"]))
            xy = config["world"]["workstation_xy_m"]
            scale = config["world"]["bench_scale"]
            bench.AddTranslateOp().Set(Gf.Vec3d(xy[0], xy[1], -config["world"]["bench_asset_top_z_m"]))
            bench.AddScaleOp().Set(Gf.Vec3f(scale, scale, scale))
            robot_report = robot.build_robot(stage, pack, config)
            camera_report = cameras.build_cameras(stage, assets, config, robot_report)
            block_report = jenga.build(stage, assets["jenga_block"], config)
            parts = ([{"prim_path": "/World/Bench", "asset_id": config["assets"]["bench"], "bom_id": "bench"}]
                     + robot_report["parts"] + camera_report["parts"] + block_report["parts"])
            required_assets = {config["assets"][role] for role in ROLES if role != "materials"}
            missing = required_assets - {part["asset_id"] for part in parts}
            if missing:
                raise ValueError(f"Physical assets missing from BOM part mappings: {sorted(missing)}")
            _tag_parts(stage, parts)
            from sim_tools.compat import composition_errors
            errors = composition_errors(stage)
            if errors:
                raise RuntimeError(f"Workcell has USD composition errors: {errors}")
            self._report = {"state": "ready", "mode": mode, "backend": "physx", "timeline": "stopped",
                            "gravity_m_s2": -9.81 if mode == "physics" else 0.0, "tabletop_z_m": 0.0,
                            "pack": copy.deepcopy(pack.manifest), "robot": robot_report,
                            "cameras": camera_report["cameras"], "jenga": block_report, "parts": parts,
                            "limitations": robot_report["limitations"] + camera_report["limitations"] + block_report["limitations"]}
        except Exception as exc:
            self._report = {"state": "failed", "mode": mode, "error": str(exc), "stage_replaced": True}
            raise
        return self.status()

    def shutdown(self):
        self._report = {"state": "shutdown", "stage_preserved": True}
        return self.status()
