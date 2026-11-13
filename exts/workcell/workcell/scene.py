"""Pack-backed workcell assembly, independent of the retired work cell mixins."""

import copy
import math
from pathlib import Path

ROLES = ("robot", "bench", "base_plate", "bota", "quick_changer", "rg2", "finger_pad",
         "d415", "wrist_mount", "overhead_mount", "overhead_release", "overhead_shelf",
         "jenga_block", "materials")


NEWTON_REFUSAL = (
    "The UR5e work cell is not yet qualified on Newton, the selected engine; no scene was built and no engine was switched. "
    "Live findings (Isaac Sim 6.2, Newton 1.6.1): the bench's triangle-mesh colliders overflow Newton's triangle-pair buffer; "
    "the separate RG2 articulation is mis-indexed by Newton's USD importer, ending in MuJoCo's error that a moving body's mass "
    "and inertia must exceed mjMINVAL; the pack's drive gains are unstable in "
    "MuJoCo-Warp; the RG2 closed chain did not move under command; and the flange force/torque read is unqualified. "
    "For the work cell use a PhysX session: `run.sh --physics-backend physx`, `[sim] physics_backend = \"physx\"` in the "
    "twinsmith config, or TWINSMITH_PHYSICS_BACKEND=physx. Details: docs/OPEN-ITEMS.md, \"Work cell on Newton\"."
)


def _require_physx():
    from .physics_compat import selected_engine

    engine = selected_engine()
    if engine != "physx":
        raise RuntimeError(f"{NEWTON_REFUSAL} Selected engine: {engine}.")


def _bom_rows(config, config_path=None):
    from . import bom, hardware_config

    selected = Path(config_path or hardware_config.DEFAULT_CONFIG).resolve()
    path = config.get("documentation", {}).get("bom_csv", "../workcell/bom.csv")
    rows = bom.load_bom(selected.parent / path)
    bom.verify_config_assets(config, rows)
    quantity = next(row["quantity"] for row in rows if row["asset_id"] == config["assets"]["jenga_block"])
    if quantity != config["jenga"]["tower_layers_built"] * 3:
        raise ValueError("Jenga layer count must match the selected BOM block quantity")
    return rows


def _preflight(config_path, mode):
    from . import asset_pack, hardware_config, jenga as tower

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
    tower.realize(config)
    _bom_rows(config, config_path)
    pack = asset_pack.resolve_pack(config)
    pack.verify_dependencies()
    assets = {}
    for role in ROLES:
        path = Path(pack.asset(roles[role]))
        if not path.is_absolute() or not path.is_file():
            raise ValueError(f"asset {role} must resolve to an existing absolute file")
        assets[role] = path
    return config, raw, pack, assets


def _configure_robot_gravity(stage, assembly_path, disabled):
    from pxr import PhysxSchema, Usd, UsdPhysics

    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for prim in Usd.PrimRange(stage.GetPrimAtPath(assembly_path)):
            if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                PhysxSchema.PhysxRigidBodyAPI.Apply(prim).CreateDisableGravityAttr().Set(disabled)


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


def _finish_scene(stage, config, assets, parts, rows):
    from pxr import Usd, UsdGeom, UsdPhysics, UsdShade
    from . import bom

    materials = UsdGeom.Scope.Define(stage, "/World/Materials")
    materials.GetPrim().GetReferences().AddReference(str(assets["materials"]))
    for role, name, section in (("jenga_block", "Wood", "jenga"),
                                ("finger_pad", "BlackPad", "finger_pads")):
        material = UsdShade.Material.Get(stage, f"/World/Materials/{name}")
        if not material:
            raise ValueError(f"Pack materials must define {name}")
        physics = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
        physics.CreateStaticFrictionAttr().Set(config[section]["static_friction"])
        physics.CreateDynamicFrictionAttr().Set(config[section]["dynamic_friction"])
        for part in parts:
            if part["asset_id"] != config["assets"][role]:
                continue
            for prim in Usd.PrimRange(stage.GetPrimAtPath(part["prim_path"])):
                if prim.IsA(UsdGeom.Gprim) or str(prim.GetPath()) == part["prim_path"]:
                    binding = UsdShade.MaterialBindingAPI.Apply(prim)
                    binding.Bind(material)
                    if role != "jenga_block":
                        binding.Bind(material, materialPurpose="physics")
    bench_meshes = 0
    for prim in Usd.PrimRange(stage.GetPrimAtPath("/World/Bench")):
        if prim.IsA(UsdGeom.Mesh):
            UsdPhysics.CollisionAPI.Apply(prim)
            UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr().Set("none")
            bench_meshes += 1
    if not bench_meshes:
        raise ValueError("Bench pack asset has no editable mesh colliders")
    bom.verify_config_assets(config, rows)
    return bom.audit_parts(parts, rows, stage=stage, strict=True)


def _replace_tower(stage, config, assets, rows, parts, profile=None, seed=None):
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics
    from . import jenga

    root = stage.GetRootLayer()
    if any(layer.GetPrimAtPath("/World/Jenga") for layer in stage.GetLayerStack() if layer != root):
        raise RuntimeError("Jenga has competing layer opinions; rebuild the workcell first")
    temporary = Usd.Stage.CreateInMemory("workcell-tower.usda", stage.GetPathResolverContext())
    UsdGeom.SetStageUpAxis(temporary, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(temporary, 1.0)
    UsdPhysics.SetStageKilogramsPerUnit(temporary, 1.0)
    report = jenga.build(temporary, assets["jenga_block"], config, profile=profile, seed=seed)
    updated = [part for part in parts if not part["prim_path"].startswith("/World/Jenga/")] + report["parts"]
    snapshot = root.ExportToString()
    try:
        with Usd.EditContext(stage, root):
            stage.RemovePrim("/World/Jenga")
            if not Sdf.CopySpec(temporary.GetRootLayer(), "/World/Jenga", root, "/World/Jenga"):
                raise RuntimeError("Could not replace Jenga tower")
            _tag_parts(stage, updated)
            audit = _finish_scene(stage, config, assets, updated, rows)
            if stage.GetCompositionErrors():
                raise RuntimeError("Replacement tower has USD composition errors")
    except Exception:
        root.ImportFromString(snapshot)
        raise
    return report, updated, audit


class Workcell:
    def __init__(self):
        self._report = {"state": "idle"}
        self._runtime = None

    def status(self):
        return copy.deepcopy(self._report)

    async def quick_start(self, config_path=None, mode="physics", discard_changes=False):
        if type(discard_changes) is not bool:
            raise ValueError("discard_changes must be a boolean")
        config, raw, pack, assets = _preflight(config_path, mode)
        rows = _bom_rows(config, config_path)
        _require_physx()
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
        self._runtime = None
        self._report = {"state": "building", "mode": mode}
        try:
            stage = context.get_stage()
            UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
            UsdGeom.SetStageMetersPerUnit(stage, 1.0)
            UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
            world = UsdGeom.Xform.Define(stage, "/World")
            stage.SetDefaultPrim(world.GetPrim())
            physics = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
            physics.CreateGravityDirectionAttr().Set(Gf.Vec3f(0, 0, -1))
            physics.CreateGravityMagnitudeAttr().Set(9.81)
            light = UsdLux.DistantLight.Define(stage, "/World/Light")
            light.CreateIntensityAttr().Set(config["lighting"]["key_intensity"])
            dome = UsdLux.DomeLight.Define(stage, "/World/DomeLight")
            dome.CreateIntensityAttr().Set(config["lighting"]["dome_intensity"])
            bench = UsdGeom.Xform.Define(stage, "/World/Bench")
            bench.GetPrim().GetReferences().AddReference(str(assets["bench"]))
            xy = config["world"]["workstation_xy_m"]
            scale = config["world"]["bench_scale"]
            bench.AddTranslateOp().Set(Gf.Vec3d(xy[0], xy[1], -config["world"]["bench_asset_top_z_m"]))
            bench.AddScaleOp().Set(Gf.Vec3f(scale, scale, scale))
            robot_report = robot.build_robot(stage, pack, config)
            _configure_robot_gravity(stage, robot_report["assembly_path"], mode == "mirror_real")
            camera_report = cameras.build_cameras(stage, assets, config, robot_report)
            block_report = jenga.build(stage, assets["jenga_block"], config)
            parts = ([{"prim_path": "/World/Bench", "asset_id": config["assets"]["bench"], "bom_id": "bench"}]
                     + robot_report["parts"] + camera_report["parts"] + block_report["parts"])
            required_assets = {config["assets"][role] for role in ROLES if role != "materials"}
            missing = required_assets - {part["asset_id"] for part in parts}
            if missing:
                raise ValueError(f"Physical assets missing from BOM part mappings: {sorted(missing)}")
            _tag_parts(stage, parts)
            audit = _finish_scene(stage, config, assets, parts, rows)
            from sim_tools.compat import composition_errors
            errors = composition_errors(stage)
            if errors:
                raise RuntimeError(f"Workcell has USD composition errors: {errors}")
            self._report = {"state": "ready", "mode": mode, "backend": "physx", "timeline": "stopped",
                            "gravity_m_s2": -9.81, "robot_gravity_disabled": mode == "mirror_real", "tabletop_z_m": 0.0,
                            "pack": copy.deepcopy(pack.manifest), "robot": robot_report,
                            "cameras": camera_report["cameras"], "jenga": block_report, "parts": parts,
                            "bom_audit": audit,
                            "limitations": robot_report["limitations"] + camera_report["limitations"] + block_report["limitations"]}
            self._runtime = (stage, config, assets, rows)
        except Exception as exc:
            self._report = {"state": "failed", "mode": mode, "error": str(exc), "stage_replaced": True}
            raise
        return self.status()

    def build_jenga(self, profile=None, seed=None):
        if self._runtime is None or self._report["state"] != "ready":
            raise RuntimeError("Build the workcell before rebuilding its tower")
        import omni.usd
        import omni.timeline
        stage, config, assets, rows = self._runtime
        if omni.usd.get_context().get_stage() != stage:
            raise RuntimeError("Workcell stage changed; quick_start is required")
        if not omni.timeline.get_timeline_interface().is_stopped():
            raise RuntimeError("Stop the timeline before rebuilding the tower")
        _require_physx()
        report, parts, audit = _replace_tower(stage, config, assets, rows, self._report["parts"], profile, seed)
        self._report.update(jenga=report, parts=parts, bom_audit=audit)
        return copy.deepcopy(report)

    def shutdown(self):
        self._runtime = None
        self._report = {"state": "shutdown", "stage_preserved": True}
        return self.status()
