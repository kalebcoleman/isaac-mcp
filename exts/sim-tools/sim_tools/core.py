"""USD operations. Session-layer-only edits, no Kit update/step calls."""

import math
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
from pxr import Ar, Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdUtils
from isaac_mcp.project_assets import (
    Broker,
    AssetError,
    refuse,
    relative,
    open_dir,
    read_regular,
)

CAP = 10000


def vector(value, n):
    if (
        not isinstance(value, (list, tuple))
        or len(value) != n
        or any(
            type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 1e8
            for x in value
        )
    ):
        refuse("INVALID_POSE", f"Expected {n} finite numbers with magnitude <=1e8.")
    return value


def path(value, root=False):
    if (
        not isinstance(value, str)
        or len(value) > 240
        or not Sdf.Path.IsValidPathString(value)
    ):
        refuse("INVALID_PRIM_PATH", "Invalid USD path.")
    p = Sdf.Path(value)
    if value == "/" and root:
        return p
    if (
        not p.IsAbsolutePath()
        or not p.IsPrimPath()
        or p.ContainsPrimVariantSelection()
        or len(p.GetPrefixes()) > 16
    ):
        refuse(
            "INVALID_PRIM_PATH",
            "Absolute prim path without variants, at most 16 levels required.",
        )
    return p


def bounded(stage, root, limit):
    if type(limit) is not int or not 1 <= limit <= 200:
        refuse("LIMIT", "limit must be 1..200.")
    p = stage.GetPrimAtPath(path(root, True))
    if not p:
        refuse("NOT_FOUND", "No prim at requested root.")
    it = iter(Usd.PrimRange(p, Usd.PrimAllPrimsPredicate))
    nodes = []
    for prim in it:
        if prim.IsPseudoRoot():
            continue
        if len(nodes) == limit:
            return nodes, True
        nodes.append(prim)
    return nodes, False


def matrix(m):
    return [[float(m[i][j]) for j in range(4)] for i in range(4)]


def facts(prim):
    return dict(
        rigid_body=prim.HasAPI(UsdPhysics.RigidBodyAPI),
        collision=prim.HasAPI(UsdPhysics.CollisionAPI),
        articulation_root=prim.HasAPI(UsdPhysics.ArticulationRootAPI),
        joint=prim.IsA(UsdPhysics.Joint),
        mass_authored=prim.HasAttribute("physics:mass")
        and prim.GetAttribute("physics:mass").HasAuthoredValueOpinion(),
        mass=prim.GetAttribute("physics:mass").Get()
        if prim.HasAttribute("physics:mass")
        else None,
        runtime_readiness="unknown",
    )


class Scene:
    def __init__(self, get_stage, timeline, broker=None, output=None):
        self.get_stage = get_stage
        self.timeline = timeline
        self.broker = broker or Broker()
        self.output = Path(
            output
            or os.getenv(
                "ISAAC_MCP_SCENE_OUTPUT",
                tempfile.gettempdir() + "/isaac-mcp-scenes-" + str(os.getpid()),
            )
        )
        self.session_id = uuid.uuid4().hex
        self._stage = None
        self.stage_id = None
        self.owned = set()
        self.runtime_assets = {}
        self._runtime_edit_target = None
        self.device_error = None

    def require_runtime(self):
        if self.device_error:
            refuse(
                "DEVICE_ERROR",
                "A native GPU error was reported in this session. Save/inspect/Stop remain available; restart the disposable Kit before runtime or sensor operations. "
                + self.device_error,
            )

    def begin_runtime_edit(self, stage):
        """Keep native physics writeback above session-authored spawn poses."""
        if self._runtime_edit_target is not None:
            self.check_runtime_edit(stage)
            return
        if not self.timeline.is_stopped():
            refuse(
                "TIMELINE_ALREADY_ACTIVE",
                "Stop before the first generic play so physics writeback can acquire the session edit target explicitly.",
            )
        self._runtime_edit_target = stage.GetEditTarget()
        stage.SetEditTarget(stage.GetSessionLayer())

    def check_runtime_edit(self, stage):
        if (
            self._runtime_edit_target is None
            or stage.GetEditTarget().GetLayer() != stage.GetSessionLayer()
        ):
            refuse(
                "EDIT_TARGET_CHANGED",
                "Physics writeback requires the managed session edit target. Stop, inspect the selected layer, then play with sim.timeline.control before stepping.",
            )

    def end_runtime_edit(self, stage):
        previous = self._runtime_edit_target
        if previous is None:
            return True
        if stage.GetEditTarget().GetLayer() != stage.GetSessionLayer():
            self._runtime_edit_target = None
            return False  # Preserve a target deliberately chosen by an external editor.
        stage.SetEditTarget(previous)
        self._runtime_edit_target = None
        return True

    def stage(self):
        self.broker.require_profile()
        st = self.get_stage()
        if st is None:
            refuse(
                "NO_STAGE",
                "No stage is open; open a disposable stage in the selected application.",
            )
        if self._stage != st:
            if self._stage is not None:
                self.end_runtime_edit(self._stage)
            self._stage = st
            self.stage_id = uuid.uuid4().hex
            self.owned.clear()
        return st

    def target(self, session_id, stage_id, mutation=False):
        stage = self.stage()
        if session_id != self.session_id or stage_id != self.stage_id:
            refuse(
                "STALE_TARGET",
                "Session or stage changed. Review current_target and resend its IDs explicitly; this call was not retargeted.",
                current_target=dict(
                    session_id=self.session_id,
                    stage_id=self.stage_id,
                    pid=os.getpid(),
                    source=stage.GetRootLayer().identifier,
                    default_prim=str(stage.GetDefaultPrim().GetPath())
                    if stage.GetDefaultPrim()
                    else None,
                    meters_per_unit=UsdGeom.GetStageMetersPerUnit(stage),
                    up_axis=str(UsdGeom.GetStageUpAxis(stage)),
                ),
            )
        if mutation and not self.timeline.is_stopped():
            refuse(
                "TIMELINE_ACTIVE",
                "Stop the timeline before authored edits; paused physics also retains runtime state.",
            )
        return stage

    def close_stage(self):
        """Release the old USD object and lease at CLOSING, not on the next call."""
        if self._stage is not None:
            self.end_runtime_edit(self._stage)
        self._stage = None
        self.stage_id = None
        self.owned.clear()

    def stage_info(self):
        stage = self.stage()
        n = 0
        for _ in stage.TraverseAll():
            n += 1
            if n > CAP:
                break
        return dict(
            session_id=self.session_id,
            stage_id=self.stage_id,
            pid=os.getpid(),
            source=stage.GetRootLayer().identifier,
            default_prim=str(stage.GetDefaultPrim().GetPath())
            if stage.GetDefaultPrim()
            else None,
            meters_per_unit=UsdGeom.GetStageMetersPerUnit(stage),
            up_axis=str(UsdGeom.GetStageUpAxis(stage)),
            prim_count=min(n, CAP),
            truncated=n > CAP,
            playing=self.timeline.is_playing(),
            stopped=self.timeline.is_stopped(),
            time_s=self.timeline.get_current_time(),
            profile="trusted_project",
            sandboxed=False,
            staging=str(self.broker.staging) if self.broker.staging else None,
            usd_version=list(Usd.GetVersion()),
            runtime_edit_target_managed=self._runtime_edit_target is not None,
            device_error=self.device_error,
        )

    def prim(self, stage, value):
        p = stage.GetPrimAtPath(path(value))
        if not p:
            refuse("NOT_FOUND", "No prim at " + value)
        return p

    def get(self, stage, path):
        p = self.prim(stage, path)
        xf = UsdGeom.Xformable(p)
        local = xf.GetLocalTransformation(Usd.TimeCode.Default()) if xf else None
        world = xf.ComputeLocalToWorldTransform(Usd.TimeCode.Default()) if xf else None
        return dict(
            path=str(p.GetPath()),
            type=str(p.GetTypeName()),
            active=p.IsActive(),
            local_matrix=matrix(local) if local is not None else None,
            world_matrix=matrix(world) if world is not None else None,
            world_position=list(world.ExtractTranslation())
            if world is not None
            else None,
            units="stage units",
            time_sample="default",
            matrix_convention="row vectors; translation in last row",
            physics=facts(p),
            pose_source="USD; not a runtime physics tensor observation",
        )

    def pose(self, stage, parent, position, orientation, frame, scale=(1, 1, 1)):
        vector(position, 3)
        vector(orientation, 4)
        if abs(sum(x * x for x in orientation) - 1) > 1e-5:
            refuse("INVALID_POSE", "orientation must be a unit quaternion [w,x,y,z].")
        if frame not in ("local", "world"):
            refuse("INVALID_FRAME", "frame must be local or world.")
        m = Gf.Matrix4d(1)
        m.SetRotate(Gf.Quatd(orientation[0], Gf.Vec3d(*orientation[1:])))
        vector(scale, 3)
        if any(not 1e-6 <= x <= 1e4 for x in scale):
            refuse("INVALID_POSE", "Positive scale 1e-6..1e4 required.")
        m = Gf.Matrix4d().SetScale(Gf.Vec3d(*scale)) * m
        m.SetTranslateOnly(Gf.Vec3d(*position))
        if frame == "world" and not parent.IsPseudoRoot():
            pm = UsdGeom.XformCache().GetLocalToWorldTransform(parent)
            if abs(pm.GetDeterminant()) < 1e-10:
                refuse("UNSUPPORTED_TRANSFORM", "Parent matrix is singular.")
            m = m * pm.GetInverse()
        return m

    def new_path(self, stage, value):
        p = path(value)
        if stage.GetPrimAtPath(p):
            refuse("CONFLICT", "Destination already exists; no overwrite.")
        parent = stage.GetPrimAtPath(p.GetParentPath())
        if (
            not parent
            or not parent.IsActive()
            or parent.IsInstance()
            or parent.IsInstanceProxy()
        ):
            refuse(
                "INVALID_PARENT", "Parent must exist, be active and not an instance."
            )
        if not stage.GetSessionLayer().permissionToEdit:
            refuse("READ_ONLY", "Session layer is not editable.")
        return p, parent

    def create(
        self,
        stage,
        path,
        kind,
        size=1,
        rigid=False,
        position=(0, 0, 0),
        orientation=(1, 0, 0, 0),
        frame="local",
        collision=False,
        scale=(1, 1, 1),
    ):
        p, parent = self.new_path(stage, path)
        if kind not in (
            "Cube",
            "Sphere",
            "Cylinder",
            "Capsule",
            "Cone",
            "Xform",
            "PhysicsScene",
        ):
            refuse(
                "UNSUPPORTED_TYPE",
                "Supported types: Cube, Sphere, Xform, PhysicsScene.",
            )
        if (
            type(size) not in (int, float)
            or not math.isfinite(size)
            or not 0 < size <= 10000
        ):
            refuse("INVALID_SIZE", "size must be finite and 0<size<=10000.")
        if (
            type(rigid) is not bool
            or rigid
            and kind not in ("Cube", "Sphere", "Cylinder", "Capsule", "Cone")
        ):
            refuse("INVALID_PHYSICS", "rigid requires Cube or Sphere.")
        if kind == "PhysicsScene" and any(
            p.IsA(UsdPhysics.Scene) for p in stage.Traverse()
        ):
            refuse("CONFLICT", "A PhysicsScene already exists.")
        ancestor = parent
        while ancestor and not ancestor.IsPseudoRoot():
            if rigid and ancestor.HasAPI(UsdPhysics.RigidBodyAPI):
                refuse("NESTED_BODY", "Ancestor already carries a rigid body.")
            ancestor = ancestor.GetParent()
        m = self.pose(stage, parent, position, orientation, frame, scale)
        try:
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                if kind == "PhysicsScene":
                    if list(position) != [0, 0, 0] or list(orientation) != [1, 0, 0, 0]:
                        refuse("INVALID_POSE", "PhysicsScene does not have a pose.")
                    prim = UsdPhysics.Scene.Define(stage, p).GetPrim()
                else:
                    prim = stage.DefinePrim(p, kind)
                    if kind == "Cube":
                        UsdGeom.Cube(prim).CreateSizeAttr(size)
                    if kind == "Sphere":
                        UsdGeom.Sphere(prim).CreateRadiusAttr(size / 2)
                    UsdGeom.Xformable(prim).AddTransformOp().Set(m)
                    if kind in ("Cylinder", "Capsule", "Cone"):
                        geometry = getattr(UsdGeom, kind)(prim)
                        geometry.CreateRadiusAttr(size / 2)
                        geometry.CreateHeightAttr(size)
                    if rigid or collision:
                        UsdPhysics.CollisionAPI.Apply(prim)
                    if rigid:
                        UsdPhysics.RigidBodyAPI.Apply(prim)
            self.owned.add(str(p))
            return self.get(stage, str(p))
        except Exception:
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                stage.RemovePrim(p)
            raise

    def transform(
        self,
        stage,
        path,
        position=(0, 0, 0),
        orientation=(1, 0, 0, 0),
        frame="local",
        scale=(1, 1, 1),
    ):
        prim = self.prim(stage, path)
        if path not in self.owned or not prim.IsActive() or prim.IsInstanceProxy():
            refuse(
                "NOT_OWNED",
                "Only active wrapper/primitives created by this pack may be transformed.",
            )
        xf = UsdGeom.Xformable(prim)
        ops = xf.GetOrderedXformOps() if xf else []
        if (
            len(ops) != 1
            or ops[0].GetOpType() != UsdGeom.XformOp.TypeTransform
            or ops[0].GetNumTimeSamples()
            or xf.GetResetXformStack()
        ):
            refuse(
                "UNSUPPORTED_TRANSFORM",
                "Expected one static transform op and no reset stack.",
            )
        m = self.pose(stage, prim.GetParent(), position, orientation, frame, scale)
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            ops[0].Set(m)
        return self.get(stage, path)

    def inspect_asset(self, snapshot_id, manifest_sha256):
        filename, manifest = self.broker.verify(snapshot_id, manifest_sha256)
        # Trusted profile only: this API invokes native parsers/resolvers. Its output
        # diagnoses dependencies; it is NOT pre-load security enforcement.
        layers, assets, missing = UsdUtils.ComputeAllDependencies(
            Sdf.AssetPath(str(filename))
        )
        root = filename.parents[len(Path(manifest["entry"]).parts) - 1]
        outside = []
        runtime_resolved = []
        for dep in [str(layer.realPath) for layer in layers] + list(assets):
            known = next(
                (k for k, v in self.runtime_assets.items() if dep == v["path"]), None
            )
            if known:
                runtime_resolved.append(known)
                continue
            try:
                package_member = None
                outer = dep
                if Ar.IsPackageRelativePath(dep):
                    outer, package_member = Ar.SplitPackageRelativePathOuter(dep)
                    relative(package_member)
                rel = str(Path(outer).relative_to(root))
                relative(rel)
                if rel not in manifest["files"]:
                    outside.append(dep)
                if package_member is not None and package_member not in manifest.get(
                    "packages", {}
                ).get(rel, {}).get("members", {}):
                    outside.append(dep)
            except (ValueError, AssetError):
                outside.append(dep)
        runtime_used = runtime_resolved + [
            str(x) for x in missing if str(x) in self.runtime_assets
        ]
        missing = [x for x in missing if str(x) not in self.runtime_assets]
        if missing or outside:
            refuse(
                "DEPENDENCIES",
                "Unresolved or outside-snapshot dependencies: "
                + repr(dict(missing=list(missing)[:12], outside=outside[:12]))[:1500],
            )
        asset = Usd.Stage.Open(str(filename))
        if not asset or not asset.GetDefaultPrim():
            refuse("DEFAULT_PRIM", "Asset needs a valid defaultPrim.")
        count = 0
        for prim in asset.TraverseAll():
            count += 1
            if count > CAP:
                refuse("LIMIT", "Asset exceeds 10000 composed prims.")
            # Explicitly refuse known script-bearing/graph prims even in trusted mode.
            # Not a claim of comprehensive script detection or execution prevention.
            if prim.GetTypeName() in (
                "OmniGraph",
                "OmniGraphNode",
            ) or prim.HasAttribute("node:type"):
                refuse(
                    "SCRIPT_GRAPH",
                    "OmniGraph/script-bearing assets are not supported by this importer.",
                )
        return (
            filename,
            asset,
            dict(
                snapshot_id=snapshot_id,
                manifest_sha256=manifest_sha256,
                default_prim=str(asset.GetDefaultPrim().GetPath()),
                meters_per_unit=UsdGeom.GetStageMetersPerUnit(asset),
                up_axis=str(UsdGeom.GetStageUpAxis(asset)),
                prim_count=count,
                dependency_layers=len(layers),
                dependency_assets=len(assets),
                packages=[
                    dict(
                        path=p,
                        default_layer=v["default_layer"],
                        member_count=len(v["members"]),
                        extracted=False,
                    )
                    for p, v in list(manifest.get("packages", {}).items())[:12]
                ],
                packages_truncated=len(manifest.get("packages", {})) > 12,
                runtime_dependencies={k: self.runtime_assets[k] for k in runtime_used},
                dependency_check="UsdUtils.ComputeAllDependencies; trusted, not a containment proof",
                sandboxed=False,
            ),
        )

    def import_asset(self, stage, snapshot_id, manifest_sha256, prim_path, **pose):
        p, parent = self.new_path(stage, prim_path)
        m = self.pose(
            stage,
            parent,
            pose.get("position", [0, 0, 0]),
            pose.get("orientation", [1, 0, 0, 0]),
            pose.get("frame", "local"),
        )
        filename, asset, info = self.inspect_asset(snapshot_id, manifest_sha256)
        if info["up_axis"] != UsdGeom.GetStageUpAxis(stage) or not math.isclose(
            info["meters_per_unit"], UsdGeom.GetStageMetersPerUnit(stage), rel_tol=1e-9
        ):
            refuse(
                "UNITS_MISMATCH",
                "Asset and target up-axis/units must match; no automatic conversion.",
            )
        try:
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                wrapper = UsdGeom.Xform.Define(stage, p)
                wrapper.AddTransformOp().Set(m)
                child = stage.DefinePrim(p.AppendChild("Asset"))
                if (
                    not child.GetReferences().AddReference(str(filename))
                    or not child.IsValid()
                ):
                    refuse("IMPORT_FAILED", "Reference could not be authored/composed.")
                errors = stage.GetCompositionErrors()
                if errors:
                    refuse("COMPOSITION_ERROR", str(errors)[:1500])
            self.owned.add(str(p))
            return dict(
                **info,
                prim=self.get(stage, str(p)),
                asset_path=str(p.AppendChild("Asset")),
                source_preserved=True,
            )
        except Exception:
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                stage.RemovePrim(p)
            raise

    def validate_project_materials(self, snapshot_id, manifest_sha256):
        """Validate bounded literal MDL and MaterialX project dependencies."""
        import xml.etree.ElementTree as ET

        filename, manifest = self.broker.verify(snapshot_id, manifest_sha256)
        root = filename.parents[len(Path(manifest["entry"]).parts) - 1]
        layers, assets, missing = UsdUtils.ComputeAllDependencies(
            Sdf.AssetPath(str(filename))
        )
        # A standalone resolver can report a known Kit material by name rather
        # than by its installed path. Do not accept that fallback if the file
        # recorded at startup is gone or has changed.
        for dependency in missing:
            runtime = self.runtime_assets.get(str(dependency))
            if runtime is not None:
                runtime_path = Path(runtime["path"])
                if (
                    not runtime_path.is_file()
                    or hashlib.sha256(runtime_path.read_bytes()).hexdigest()
                    != runtime["sha256"]
                ):
                    refuse(
                        "DEPENDENCIES",
                        "The required installed runtime material is missing or changed.",
                    )
        dependency_names = [str(layer.realPath) for layer in layers] + [
            str(a) for a in assets
        ]
        for package, details in manifest.get("packages", {}).items():
            package_path = str(root / package)
            used = package == manifest["entry"] or any(
                package_path in value for value in dependency_names
            )
            if used and any(
                Path(member).suffix.lower() in {".mdl", ".mtlx"}
                for member in details.get("members", {})
            ):
                refuse(
                    "DEPENDENCIES_UNSUPPORTED",
                    "USDZ packages containing MDL or MaterialX need package-aware material validation: "
                    + package,
                )
        textures = []
        other_mdl_resources = []
        materialx = []

        def reference(parent, value, kind):
            if (
                not value
                or value.startswith("/")
                or ":" in value
                or "\\" in value
                or any(c in value for c in "<>$#?{}")
            ):
                refuse(
                    "DEPENDENCIES_UNSUPPORTED",
                    kind
                    + " contains an absolute, network or dynamic reference: "
                    + value,
                )
            target = Path(os.path.normpath(str(parent / value)))
            try:
                rel = target.relative_to(root).as_posix()
                relative(rel)
            except ValueError:
                refuse("DEPENDENCIES", kind + " escapes the approved project: " + value)
            if rel not in manifest["files"]:
                refuse("DEPENDENCIES", "Missing " + kind + ": " + rel)
            return target, rel

        pending_mtlx = []
        # USD releases disagree on whether MaterialX is reported as a layer or
        # an asset. Inspect both collections and de-duplicate their paths.
        for dependency in dict.fromkeys(dependency_names):
            dep = Path(str(dependency))
            runtime = next(
                (v for v in self.runtime_assets.values() if str(dep) == v["path"]),
                None,
            )
            if runtime is not None:
                # Kit may report its built-in MDL as resolved, while standalone
                # OpenUSD reports the same module as an unresolved name. Both
                # forms are accepted only for the pinned installed runtime file.
                if (
                    not dep.is_file()
                    or hashlib.sha256(dep.read_bytes()).hexdigest() != runtime["sha256"]
                ):
                    refuse(
                        "DEPENDENCIES",
                        "The required installed runtime material is missing or changed.",
                    )
                continue
            if dep.suffix.lower() == ".mtlx":
                pending_mtlx.append(dep)
                continue
            if dep.suffix.lower() != ".mdl":
                continue
            try:
                mdl_rel = dep.relative_to(root).as_posix()
            except ValueError:
                refuse(
                    "DEPENDENCIES_UNSUPPORTED",
                    "External MDL module is outside the approved project snapshot.",
                )
            relative(mdl_rel)
            if mdl_rel not in manifest["files"]:
                refuse(
                    "DEPENDENCIES", "MDL material missing from the snapshot: " + mdl_rel
                )
            text = dep.read_text(encoding="utf-8")
            # Comments do not describe texture resources. For safety reject
            # escaped/expressive arguments rather than guessing at MDL syntax.
            text = re.sub(
                r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*',
                lambda match: match.group() if match.group().startswith('"') else "",
                text,
                flags=re.S,
            )
            # We support only absolute MDL module names. A relative or otherwise
            # nonstandard import could hide another file with its own resources.
            without_using = re.sub(
                r"\busing\s+::[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*\s+import\s+[^;]+;",
                "",
                text,
            )
            if re.search(
                r"\busing\s+|\bimport(?!\s*::)\s+|#\s*include\b", without_using
            ):
                refuse(
                    "DEPENDENCIES_UNSUPPORTED",
                    "A relative or unsupported MDL import cannot be validated: "
                    + mdl_rel,
                )
            allowed = {
                "OmniPBR",
                "tex",
                "state",
                "base",
                "df",
                "math",
                "anno",
                "limits",
                "debug",
                "scene",
            }
            modules = re.findall(
                r"\b(?:using|import)\s+::([A-Za-z_][A-Za-z_0-9]*)", text
            )
            if any(module not in allowed for module in modules):
                refuse(
                    "DEPENDENCIES_UNSUPPORTED",
                    "A custom MDL module cannot be validated: " + mdl_rel,
                )
            if "OmniPBR" in modules:
                runtime = self.runtime_assets.get("OmniPBR.mdl")
                if (
                    not runtime
                    or not Path(runtime["path"]).is_file()
                    or hashlib.sha256(Path(runtime["path"]).read_bytes()).hexdigest()
                    != runtime["sha256"]
                ):
                    refuse(
                        "DEPENDENCIES",
                        "The required installed OmniPBR MDL module is missing or changed.",
                    )
            resource = r"(texture_(?:2d|3d|cube|ptex)|light_profile|bsdf_measurement)"
            starts = list(re.finditer(r"\b" + resource + r"\s*\(", text))
            calls = list(re.finditer(r"\b" + resource + r"\s*\(([^()]*)\)", text))
            if len(starts) != len(calls):
                refuse(
                    "DEPENDENCIES_UNSUPPORTED",
                    "A complex MDL resource expression cannot be validated: " + mdl_rel,
                )
            for call in calls:
                kind = (
                    "MDL texture"
                    if call.group(1).startswith("texture_")
                    else "MDL resource"
                )
                arg = call.group(2).strip()
                if not arg:
                    continue
                match = re.match(r'^"([^"\\]+)"(?:\s*,.*)?$', arg, flags=re.S)
                if not match or "<" in arg:
                    refuse(
                        "DEPENDENCIES_UNSUPPORTED",
                        "A non-literal, escaped or tiled MDL resource cannot be validated: "
                        + mdl_rel,
                    )
                value = match.group(1)
                _, rel = reference(dep.parent, value, kind)
                (textures if kind == "MDL texture" else other_mdl_resources).append(rel)
        visited = set()
        while pending_mtlx:
            dep = pending_mtlx.pop()
            try:
                rel = dep.relative_to(root).as_posix()
                relative(rel)
            except ValueError:
                refuse(
                    "DEPENDENCIES_UNSUPPORTED",
                    "MaterialX is outside the project snapshot.",
                )
            if rel in visited:
                continue
            if rel not in manifest["files"] or len(visited) >= 1000:
                refuse(
                    "DEPENDENCIES", "Missing or excessive MaterialX dependency: " + rel
                )
            visited.add(rel)
            data = dep.read_bytes()
            if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
                refuse(
                    "DEPENDENCIES_UNSUPPORTED",
                    "MaterialX XML declarations/entities are unsupported: " + rel,
                )
            try:
                xml = ET.fromstring(data)
            except ET.ParseError:
                refuse("DEPENDENCIES_UNSUPPORTED", "Invalid MaterialX document: " + rel)
            for element in xml.iter():
                if element.get("fileprefix"):
                    refuse(
                        "DEPENDENCIES_UNSUPPORTED",
                        "MaterialX fileprefix requires resolver support: " + rel,
                    )
                values = []
                if element.get("type") == "filename" and element.get("value"):
                    values.append(element.get("value"))
                for key in ("file", "filename", "href"):
                    if element.get(key):
                        values.append(element.get(key))
                for value in values:
                    target, name = reference(dep.parent, value, "MaterialX dependency")
                    materialx.append(name)
                    if target.suffix.lower() == ".mtlx":
                        pending_mtlx.append(target)
                    elif target.suffix.lower() in {
                        ".mdl",
                        ".usd",
                        ".usda",
                        ".usdc",
                        ".usdz",
                        ".abc",
                        ".xml",
                    }:
                        refuse(
                            "DEPENDENCIES_UNSUPPORTED",
                            "Nested material or scene dependency requires additional validation: "
                            + name,
                        )
        return dict(
            mdl_literal_textures=textures[:32],
            mdl_texture_count=len(textures),
            mdl_other_resources=other_mdl_resources[:32],
            mdl_other_resource_count=len(other_mdl_resources),
            materialx_dependencies=materialx[:32],
            materialx_dependency_count=len(materialx),
            truncated=len(textures) > 32
            or len(other_mdl_resources) > 32
            or len(materialx) > 32,
            note="Literal MDL and MaterialX paths checked. Custom MDL modules, dynamic/tiled paths and MaterialX fileprefix are refused; this is not a sandbox.",
        )

    def import_path(self, stage, snapshot_id, manifest_sha256, prim_path, **pose):
        self.inspect_asset(snapshot_id, manifest_sha256)
        materials = self.validate_project_materials(snapshot_id, manifest_sha256)
        result = self.import_asset(
            stage, snapshot_id, manifest_sha256, prim_path, **pose
        )
        result["material_checks"] = materials
        return result

    def prepare_workstation(self, snapshot_id, manifest_sha256):
        """Publish a validated trusted snapshot to the existing UR5e cache contract.

        This prepares files only; the twin's load_workstation/quick-start performs
        the scene edit. The source project and existing cache entry are never edited.
        """
        filename, asset, info = self.inspect_asset(snapshot_id, manifest_sha256)
        materials = self.validate_project_materials(snapshot_id, manifest_sha256)
        _, manifest = self.broker.verify(snapshot_id, manifest_sha256)
        source_root = filename.parents[len(Path(manifest["entry"]).parts) - 1]
        override = os.getenv("UR5E_DT_ASSET_CACHE", "").strip()
        xdg = os.getenv("XDG_CACHE_HOME", "").strip()
        cache = (
            Path(override).expanduser()
            if override
            else (Path(xdg).expanduser() if xdg else Path.home() / ".cache")
            / "ur5e-dt"
            / "assets"
        )
        if not cache.is_absolute() or ".." in cache.parts:
            refuse("CONFIG", "UR5E_DT_ASSET_CACHE must be an absolute local directory.")
        repo = Path(__file__).resolve().parents[3]
        if cache == repo or repo in cache.parents:
            refuse("CONFIG", "Workstation cache must be outside the repository.")
        for root, _ in self.broker.roots.values():
            if cache == root or cache in root.parents or root in cache.parents:
                refuse(
                    "CONFIG",
                    "Workstation cache and approved project roots must be disjoint.",
                )
        staging = self.broker.staging
        if cache == staging or cache in staging.parents or staging in cache.parents:
            refuse("CONFIG", "Workstation cache and project staging must be disjoint.")

        # Walk/create without following symlink components; final cache must be
        # owned by this user and not writable by other users.
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in cache.parts[1:]:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                new = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                os.close(fd)
                fd = new
            st = os.fstat(fd)
            if st.st_uid != os.getuid() or st.st_mode & 0o022:
                refuse(
                    "CONFIG",
                    "Workstation cache must be owned by this user and not group/world writable.",
                )
            try:
                os.mkdir("workstation", 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            wsfd = os.open(
                "workstation", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            try:
                st = os.fstat(wsfd)
                if st.st_uid != os.getuid() or st.st_mode & 0o022:
                    refuse("CONFIG", "Workstation directory is not safely owned.")
                try:
                    os.stat("vention.usd", dir_fd=wsfd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    refuse(
                        "ALREADY_EXISTS",
                        "workstation/vention.usd already exists; no files were overwritten.",
                    )
            finally:
                os.close(wsfd)
        finally:
            os.close(fd)

        workstation = cache / "workstation"
        token = uuid.uuid4().hex
        work = Path(tempfile.mkdtemp(prefix=".prepare-", dir=workstation))
        bundle = workstation / ("project-" + token)
        wrapper = workstation / (".vention-" + token + ".usda")
        published = False
        try:
            # Copy exactly the verified bounded snapshot; re-check every digest
            # while reading and reject symlink/replacement races.
            for rel, digest in manifest["files"].items():
                relative(rel)
                src = source_root / rel
                parent_fd = open_dir(src.parent)
                try:
                    before = os.stat(src.name, dir_fd=parent_fd, follow_symlinks=False)
                    data = read_regular(parent_fd, src.name, before)
                finally:
                    os.close(parent_fd)
                if hashlib.sha256(data).hexdigest() != digest:
                    refuse(
                        "SNAPSHOT_CHANGED",
                        "Snapshot changed while preparing the workstation.",
                    )
                dest = work / rel
                dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with dest.open("xb") as stream:
                    stream.write(data)
            self.broker.verify(snapshot_id, manifest_sha256)
            work.rename(bundle)
            layer = Sdf.Layer.CreateNew(str(wrapper))
            staged = Usd.Stage.Open(layer)
            root = UsdGeom.Xform.Define(staged, "/Workstation").GetPrim()
            staged.SetDefaultPrim(root)
            UsdGeom.SetStageMetersPerUnit(staged, info["meters_per_unit"])
            UsdGeom.SetStageUpAxis(staged, info["up_axis"])
            reference = "./" + bundle.name + "/" + manifest["entry"]
            if (
                not root.GetReferences().AddReference(reference)
                or staged.GetCompositionErrors()
            ):
                refuse(
                    "COMPOSITION_ERROR", "Prepared workstation wrapper did not compose."
                )
            if not layer.Save():
                refuse("FILESYSTEM", "Could not save the workstation wrapper.")
            # Do not silently accept unresolved dependencies after relocation.
            _, _, missing = UsdUtils.ComputeAllDependencies(Sdf.AssetPath(str(wrapper)))
            missing = [str(x) for x in missing if str(x) not in self.runtime_assets]
            if missing:
                refuse(
                    "DEPENDENCIES",
                    "Prepared workstation has unresolved dependencies: "
                    + repr(missing[:12]),
                )
            try:
                os.link(wrapper, workstation / "vention.usd", follow_symlinks=False)
            except FileExistsError:
                refuse(
                    "ALREADY_EXISTS",
                    "workstation/vention.usd was created concurrently; no file was overwritten.",
                )
            published = True
            return dict(
                **info,
                prepared=True,
                loaded=False,
                material_checks=materials,
                workstation_file=str(workstation / "vention.usd"),
                source_preserved=True,
                copied_project_files=len(manifest["files"]),
                next_tool="ur5e_dt__load_workstation",
                verification="Call the twin tool, then inspect /World/Vention and its visible result. The twin must use this cache and have no higher-priority workstation drop-in.",
                limitations="Trusted USD only; network/outside-snapshot dependencies are refused. USD dependency discovery cannot prove arbitrary resolver/plugin or directory dependencies complete.",
            )
        finally:
            wrapper.unlink(missing_ok=True)
            if not published:
                shutil.rmtree(work, ignore_errors=True)
                shutil.rmtree(bundle, ignore_errors=True)

    def save(self, stage, filename, runtime_poses=None):
        if self.timeline.is_playing():
            refuse(
                "TIMELINE_ACTIVE",
                "Pause or stop before saving a stable composed snapshot.",
            )
        if not isinstance(filename, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,59}\.usda", filename
        ):
            refuse(
                "INVALID_OUTPUT",
                "Use a simple basename ending .usda (<=65 characters).",
            )
        self.output.mkdir(mode=0o700, parents=True, exist_ok=True)
        from isaac_mcp.project_assets import open_dir

        fd = open_dir(self.output)
        tmp = None
        try:
            # Export to private temporary file; hard-link publication is no-replace,
            # unlike an exists check followed by Export to the destination.
            handle, tmp = tempfile.mkstemp(suffix=".usda", dir=self.output)
            os.close(handle)
            layer = stage.Flatten()
            omitted_render_paths = strip_runtime_render_state(layer)
            if runtime_poses:
                apply_snapshot_poses(layer, runtime_poses)
            if not layer.Export(tmp):
                refuse("SAVE_FAILED", "USD export returned false.")
            try:
                os.link(tmp, filename, dst_dir_fd=fd, follow_symlinks=False)
            except FileExistsError:
                refuse("CONFLICT", "Output already exists; no overwrite.")
            return dict(
                file=str(self.output / filename),
                bytes=os.stat(filename, dir_fd=fd).st_size,
                flattened=True,
                stage_switched=False,
                runtime_pose_count=len(runtime_poses or []),
                omitted_runtime_render_paths=omitted_render_paths,
                snapshot="composed USD with runtime rigid-body poses"
                if runtime_poses
                else "composed USD",
                checkpoint=False,
                dependencies="Texture files may still reference retained snapshots; do not clean those while using saved scenes.",
            )
        finally:
            os.close(fd)
            if tmp is not None:
                os.unlink(tmp)


def apply_snapshot_poses(layer, poses):
    """Apply world poses to a flattened copy, preserving scale and hierarchy.

    The live stage and its layers are never edited. Parents precede children so
    a child's local matrix is computed against the exported parent's runtime pose.
    This is a pose snapshot, not a velocity/controller/solver checkpoint.
    """
    output = Usd.Stage.Open(layer)
    original = {}
    for entry in poses:
        prim = output.GetPrimAtPath(path(entry["path"]))
        if not prim or prim.IsInstance() or prim.IsInstanceProxy():
            refuse(
                "SNAPSHOT_UNSUPPORTED",
                "Runtime body cannot be represented in the flattened output.",
            )
        original[entry["path"]] = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()
        )
    for entry in sorted(poses, key=lambda e: e["path"].count("/")):
        prim = output.GetPrimAtPath(entry["path"])
        position = vector(entry["world_position"], 3)
        q = vector(entry["orientation_wxyz"], 4)
        if abs(sum(x * x for x in q) - 1) > 1e-4:
            refuse("PHYSICS_FORMAT", "Runtime orientation is not a unit quaternion.")
        transform = Gf.Transform(original[entry["path"]])
        transform.SetTranslation(Gf.Vec3d(*position))
        transform.SetRotation(Gf.Rotation(Gf.Quatd(q[0], Gf.Vec3d(*q[1:]))))
        parent = UsdGeom.XformCache().GetLocalToWorldTransform(prim.GetParent())
        xf = UsdGeom.Xformable(prim)
        world = transform.GetMatrix()
        local = world if xf.GetResetXformStack() else world * parent.GetInverse()
        reset = xf.GetResetXformStack()
        xf.MakeMatrixXform().Set(local)
        xf.SetResetXformStack(reset)


def strip_runtime_render_state(layer):
    """Omit process-bound SyntheticData graphs from the private saved copy.

    Cameras, LiDAR definitions, materials and user Action Graphs remain. Native
    graph registrations and render-product handles cannot be reopened as USD.
    Unknown content at a native pipeline path refuses, rather than being lost.
    """
    output = Usd.Stage.Open(layer)
    known_nodes = {
        "omni.replicator.core.OgnRefTimeGate",
        "omni.syntheticdata.SdOnNewFrame",
        "omni.syntheticdata.SdOnNewRenderProductFrame",
        "omni.syntheticdata.SdPostRenderVarToHost",
        "omni.syntheticdata.SdRenderVarPtr",
        "omni.graph.nodes.GpuInteropRenderProductEntry",
        "isaacsim.sensors.rtx.IsaacCreateRTXLidarScanBuffer",
        "omni.graph.action.RationalTimeSyncGate",
        "omni.replicator.nv.ReadRpFabricTime",
        "omni.syntheticdata.SdNoOp",
        "isaacsim.core.nodes.IsaacSimulationGate",
        "isaacsim.core.nodes.IsaacReadSimulationTimeAnnotator",
        "isaacsim.core.nodes.IsaacConvertRGBAToRGB",
    }
    # Camera helpers regenerate these session-only writers on playback. A
    # flattened export includes their process handles, unlike a normal USD
    # root-layer save. Only omit recognized writers backed by a preserved
    # helper with the same topic and frame; an arbitrary ROS node is not safe
    # to discard. NVIDIA documents the helper-generated SDGPipeline as runtime
    # state, not part of the asset.
    ros_writers = {
        "isaacsim.ros2.bridge.ROS2PublishImage":
            "isaacsim.ros2.bridge.ROS2CameraHelper",
        "isaacsim.ros2.bridge.ROS2PublishCameraInfo":
            "isaacsim.ros2.bridge.ROS2CameraInfoHelper",
    }
    helpers = {}
    for prim in output.Traverse():
        node_type = prim.GetAttribute("node:type").Get()
        if node_type in ros_writers.values() and not str(prim.GetPath()).startswith("/Render/"):
            key = (node_type, prim.GetAttribute("inputs:topicName").Get(),
                   prim.GetAttribute("inputs:frameId").Get())
            helpers.setdefault(key, []).append(prim)

    def regenerable_writer(prim):
        node_type = prim.GetAttribute("node:type").Get()
        helper_type = ros_writers.get(node_type)
        writer_id = prim.GetAttribute("inputs:writerId").Get()
        key = (helper_type, prim.GetAttribute("inputs:topicName").Get(),
               prim.GetAttribute("inputs:frameId").Get())
        if not helper_type or not isinstance(writer_id, str) or not re.fullmatch(
            r"NodeWriter_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", writer_id
        ):
            return False
        for helper in helpers.get(key, []):
            # A standalone helper without its authored render-product source
            # cannot rebuild the writer after reopening.
            for source in helper.GetAttribute("inputs:renderProductPath").GetConnections():
                producer = output.GetPrimAtPath(source.GetPrimPath())
                if (producer and producer.GetAttribute("node:type").Get()
                        == "isaacsim.core.nodes.IsaacCreateRenderProduct"
                        and source.name == "outputs:renderProductPath"):
                    return True
        return False
    omitted = []
    owned_products = set()
    for prim in output.Traverse():
        name = str(prim.GetPath())
        if name == "/Orchestrator":
            # Replicator's session graph has a Python event subscription which
            # USD cannot serialize. In 1.13.4 the reopened graph makes
            # _is_tick_graph() true, skipping _subscribe_update(); render frames
            # complete but CPU annotators remain empty. Let Replicator rebuild
            # its own graph/subscription in the next process. Do not call its
            # private lifecycle APIs or mutate the live stage to repair this.
            native_nodes = {
                "OgnReadFabricTime": "omni.replicator.core.ReadFabricTime",
                "OrchestratorUpdate": "omni.replicator.core.OgnUpdateOrchestrator",
            }
            children = list(prim.GetChildren())
            if (
                prim.GetTypeName() != "OmniGraph"
                or prim.GetAttribute("evaluator:type").Get() != "push"
                or prim.GetAttribute("pipelineStage").Get() != "pipelineStageSimulation"
                or not children
                or not any(c.GetName() == "OgnReadFabricTime" for c in children)
                or any(
                    c.GetTypeName() != "OmniGraphNode"
                    or c.GetName() not in native_nodes
                    or c.GetAttribute("node:type").Get()
                    != native_nodes.get(c.GetName())
                    or c.GetChildren()
                    for c in children
                )
            ):
                refuse(
                    "SAVE_UNSUPPORTED",
                    "Unrecognized /Orchestrator content; saved copy not published. Live stage preserved.",
                )
            omitted.append(name)
            continue
        if not name.startswith("/Render/"):
            continue
        if prim.GetTypeName() == "OmniGraph" and prim.GetName() == "SDGPipeline":
            for child in Usd.PrimRange(prim):
                if child == prim:
                    continue
                if (
                    child.GetTypeName() != "OmniGraphNode"
                    or (child.GetAttribute("node:type").Get() not in known_nodes
                        and not regenerable_writer(child))
                ):
                    refuse(
                        "SAVE_UNSUPPORTED",
                        "Unknown content in native SyntheticData pipeline; saved copy not published. Live stage preserved.",
                    )
            omitted.append(name)
        elif prim.GetTypeName() == "RenderProduct" and re.fullmatch(
            r"IsaacMCP_[0-9a-f]{32}", prim.GetName()
        ):
            omitted.append(name)
            owned_products.add(prim.GetPath())
    # A user controller may reference a native graph. Dropping that dependency
    # silently would change its behavior; refuse before publishing the copy.
    omitted_paths = [Sdf.Path(name) for name in omitted]
    for prim in output.Traverse():
        if any(prim.GetPath().HasPrefix(path) for path in omitted_paths):
            continue
        for attr in prim.GetAttributes():
            if any(
                target.HasPrefix(path)
                for target in attr.GetConnections()
                for path in omitted_paths
            ):
                refuse(
                    "SAVE_UNSUPPORTED",
                    "Preserved content connects to process-bound render state; saved copy not published. Live stage preserved.",
                )
        for relationship in prim.GetRelationships():
            targets = relationship.GetTargets()
            removed = [target for target in targets
                       if any(target.HasPrefix(path) for path in omitted_paths)]
            if not removed:
                continue
            if (str(relationship.GetPath()) == "/Render/OmniverseGlobalRenderSettings.products"
                    and prim.GetTypeName() == "RenderSettings"
                    and all(target in owned_products for target in removed)):
                # Kit automatically enrolls every live render product here.
                # Remove only our omitted handles from the private copy; keep
                # user products and their order. Other dependencies refuse.
                relationship.SetTargets([target for target in targets if target not in removed])
            else:
                refuse(
                    "SAVE_UNSUPPORTED",
                    "Preserved relationship targets process-bound render state; saved copy not published. Live stage preserved.",
                    relationship=str(relationship.GetPath()),
                    targets=[str(target) for target in removed],
                )
    # Remove deepest paths first. Never edit the live stage or its layers.
    for name in sorted(omitted, key=len, reverse=True):
        output.RemovePrim(name)
    return sorted(omitted)
