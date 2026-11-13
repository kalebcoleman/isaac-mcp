"""Prim tools: bounds, delete, clone, batch create, lights, materials, attributes and physics schemas."""

import itertools
import math
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, UsdShade
from isaac_mcp.project_assets import refuse
from .core import KIT_ROOTS, bounded, vector
from .values import ATTRIBUTE_NAME, SUPPORTED_TYPES, attribute_kind, color, jsonable, scalar, typed_value


def owned(s, stage, p):
    prim = s.prim(stage, p)
    if p not in s.owned:
        refuse(
            "NOT_OWNED",
            "Operation is limited to pack-created prims: ones these tools made on this stage since it opened. "
            "Prims from an opened file do not count, even ones you made before saving.",
        )
    return prim


def bind_material(s, stage, prim_path, material_path):
    """Bind a pack-made material to a geometry prim in the session layer. Shared by sim.material.apply,
    sim.prim.create's material option and sim.prim.create_many."""
    prim = s.prim(stage, prim_path)
    material = UsdShade.Material(owned(s, stage, material_path))
    if not material or not UsdGeom.Gprim(prim):
        refuse("WRONG_TYPE", "Need geometry and a pack-owned material.")
    purpose = "physics" if material.GetPrim().HasAPI(UsdPhysics.MaterialAPI) else ""
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(material, materialPurpose=purpose)
    return dict(
        path=prim_path,
        material=str(UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial(materialPurpose=purpose)[0].GetPath()),
        purpose=purpose,
    )


CREATE_KEYS = ("path", "kind", "size", "rigid", "position", "orientation", "frame", "collision", "scale", "mass",
               "density", "material")


MAX_CREATE_MANY = 200


# Shared `defaults` that do not apply to a kind are left off it, so one list can hold a group Xform and its blocks.
SHARED_ONLY_FOR = {
    "Xform": ("rigid", "mass", "density", "collision", "material"),
    "PhysicsScene": ("rigid", "mass", "density", "collision", "material"),
    "Plane": ("rigid", "mass", "density"),
}


def create_one(s, stage, spec):
    """One prim from sim.prim.create's arguments plus an optional pack-made material. A material that cannot
    be bound removes the prim again, so a call either makes the whole prim or nothing."""
    from .placement import flat_box_warning

    spec = dict(spec)
    material = spec.pop("material", None)
    if material is not None and (not isinstance(material, str) or not material.startswith("/")):
        refuse("INVALID_VALUE", "material must be the absolute path of a material made with sim__material__create.")
    created = s.create(stage, **spec)
    warning = flat_box_warning(spec.get("kind"), spec.get("scale") or (1, 1, 1), spec.get("collision") or spec.get("rigid"))
    if material is not None:
        try:
            bind_material(s, stage, spec["path"], material)
        except Exception:
            remove_created(s, stage, [spec["path"]])
            raise
        created = {**created, "material": material}
    return created, warning


def remove_created(s, stage, paths):
    """Take back prims this call made, newest first."""
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for p in reversed(paths):
            stage.RemovePrim(p)
            s.owned.discard(p)
            getattr(s, "edits", {}).pop(p, None)


def create_many(s, stage, a):
    """Make up to MAX_CREATE_MANY prims in one call, all or none. `defaults` fills any key an item leaves out."""
    items, defaults = a.get("items"), a.get("defaults") or {}
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_CREATE_MANY:
        refuse("INVALID_VALUE", f"items must be a list of 1 to {MAX_CREATE_MANY} prim specs.")
    if not isinstance(defaults, dict):
        refuse("INVALID_VALUE", "defaults must be an object of prim arguments shared by every item.")
    for label, spec in [("defaults", defaults), *[(f"items[{i}]", item) for i, item in enumerate(items)]]:
        if not isinstance(spec, dict):
            refuse("INVALID_VALUE", f"{label} must be an object.")
        unknown = sorted(set(spec) - set(CREATE_KEYS))
        if unknown:
            refuse("INVALID_VALUE", f"{label} has unknown keys {unknown}; allowed: {list(CREATE_KEYS)}. Nothing was created.")
    made, warnings, skipped = [], [], {}
    for index, item in enumerate(items):
        spec = {**defaults, **item}
        kind = spec.get("kind")
        for key in SHARED_ONLY_FOR.get(kind, ()):
            if key in defaults and key not in item:
                spec.pop(key, None)  # a shared rigid, mass or material is for shapes, so an Xform in the list skips it
                skipped.setdefault(spec.get("path"), []).append(key)
        try:
            if "path" not in spec or "kind" not in spec:
                refuse("INVALID_VALUE", "each item needs path and kind (defaults may supply kind).")
            created, warning = create_one(s, stage, spec)
        except Exception as exc:
            remove_created(s, stage, made)
            detail = str(exc)
            refuse(
                getattr(exc, "code", None) or "BATCH_FAILED",
                f"items[{index}] ({spec.get('path')!r}) failed, so all {len(made)} earlier prims were removed and "
                f"nothing was created: {detail}",
                **{**{("item_retry_with" if k == "retry_with" else k): v for k, v in getattr(exc, "details", {}).items()},
                   "failed_index": index, "failed_path": spec.get("path")},
            )
        made.append(spec["path"])
        if warning:
            warnings.append(dict(index=index, path=spec["path"], warning=warning))
    return dict(
        count=len(made),
        paths=made,
        **({"warnings": warnings} if warnings else {}),
        **({"defaults_skipped": skipped} if skipped else {}),
        note="All items were created in one call; undo them with sim__prim__delete on each path.",
    )


def source_api_layers(stage, prim, token):
    """Layers other than this session that apply an API schema to *prim*."""
    session = stage.GetSessionLayer()
    found = []
    for spec in prim.GetPrimStack():
        if spec.layer == session:
            continue
        value = spec.GetInfo("apiSchemas")
        if value is not None and token in (list(value.prependedItems) + list(value.explicitItems)):
            found.append(spec.layer.identifier)
    return found


def remove_api_schema(stage, prim, token, alternative):
    """Remove this session's API application; refuse when another layer applies it."""
    layers = source_api_layers(stage, prim, token)
    if layers:
        refuse(
            "READ_ONLY_SOURCE",
            f"{prim.GetPath()} has {token} applied in {layers[0]}, and this tool edits only the session layer. "
            f"Edit the source file, or use {alternative} on a prim the pack created or imported.",
            source_layers=layers,
        )
    session = stage.GetSessionLayer()
    spec = session.GetPrimAtPath(prim.GetPath())
    value = spec.GetInfo("apiSchemas") if spec is not None else None
    if value is None:
        return False
    prepended = [item for item in value.prependedItems if item != token]
    explicit = [item for item in value.explicitItems if item != token]
    if (len(prepended), len(explicit)) == (len(list(value.prependedItems)), len(list(value.explicitItems))):
        return False
    if prepended or explicit:
        created = dict(prependedItems=tuple(str(item) for item in prepended))
        if explicit:
            created["explicitItems"] = tuple(str(item) for item in explicit)
        spec.SetInfo("apiSchemas", Sdf.TokenListOp.Create(**created))
    else:
        spec.ClearInfo("apiSchemas")
    return True


def foreign_prim(s, stage, p, allow_foreign):
    """The explicit route for a prim that is not pack-owned; refuses unless it was asked for."""
    prim = s.prim(stage, p)
    if not allow_foreign and p == "/World":  # allow_foreign wouldn't help, so don't send the caller there
        refuse("PROTECTED_PRIM", "World/default prim or its ancestor is protected.")
    if not allow_foreign:
        refuse(
            "NOT_OWNED",
            f"{p} was not created by these tools on this stage: prims from an opened file do not count, even "
            "ones you made before saving. Retry with allow_foreign=true to deactivate it in the session layer "
            "(it stays composed as inactive and keeps its path; source files are unchanged), or reach it through "
            "the file that defines it.",
            retry_with={"allow_foreign": True},
        )
    if prim.IsInstanceProxy() or prim.GetTypeName() == "OmniGraphNode":
        refuse(
            "PROTECTED_PRIM",
            "Cannot deactivate an instance proxy or a single graph node; "
            "act on the instance or graph, or rebuild a graph with sim__graph__build replace=true.",
        )
    if prim.GetPath().GetPrefixes()[0].name in KIT_ROOTS:
        refuse("PROTECTED_PRIM", "Kit's runtime render and viewport prims are protected.")
    return prim


MAX_SENSOR_SUBTREE = 10000


def deletable(s, stage, p, direct_sensor=False, sensor_paths=(), foreign=False):
    prim = s.prim(stage, p) if foreign else owned(s, stage, p)
    # USD traversal cannot see children below inactive prims, or registered
    # sensors removed/retyped externally. Their native resources still exist.
    if any(
        Sdf.Path(q) != prim.GetPath() and Sdf.Path(q).HasPrefix(prim.GetPath())
        for q in sensor_paths
    ):
        refuse("PROTECTED_PRIM", "Subtree contains registered sensor resources.")
    if (
        p == "/World"
        or stage.GetDefaultPrim()
        and stage.GetDefaultPrim().GetPath().HasPrefix(prim.GetPath())
    ):
        refuse("PROTECTED_PRIM", "World/default prim or its ancestor is protected.")
    # A sensor the pack registered can bring a large config subtree; its slot must stay releasable.
    # A map is one import of up to 4,000 walls, and it must stay removable as a whole.
    large = direct_sensor or p in getattr(s, "bulk_owned", ())
    if large:
        nodes = list(itertools.islice(Usd.PrimRange(prim, Usd.PrimAllPrimsPredicate), MAX_SENSOR_SUBTREE + 1))
        truncated = len(nodes) > MAX_SENSOR_SUBTREE
    else:
        nodes, truncated = bounded(stage, p, 200)
    if truncated:
        refuse("LIMIT", f"Subtree deletion exceeds {MAX_SENSOR_SUBTREE if large else 200} prims.")
    # Direct deletion releases only this sensor, never sensors below it.
    if any(
        n.HasAPI(UsdPhysics.ArticulationRootAPI)
        or (
            (not direct_sensor or str(n.GetPath()) not in (str(prim.GetPath()), direct_sensor))
            and (n.IsA(UsdGeom.Camera) or n.GetTypeName() == "OmniLidar")
        )
        or n.GetMetadata("no_delete")
        for n in nodes
    ):
        refuse(
            "PROTECTED_PRIM",
            "Subtree contains an articulation, sensor or protected prim, which cannot be removed from a scene. Leave it, use another "
            "path, or start a new scene with sim__quick_start discard_changes=true (that drops every edit; sim__stage__save keeps them first).",
        )
    return prim


class PrimTools:
    """Mixed into Advanced: uses its scene (``self.s``), sensors and ``physics_manager``."""

    def delete_foreign(self, stage, prim):
        """What the session layer can honestly do to a prim defined elsewhere. USD has no delete
        opinion and RemovePrim only edits the edit target's specs, so a prim with opinions in the
        root layer stays composed. Never edit that layer: a File > Save would write it to the source."""
        p = str(prim.GetPath())
        session = stage.GetSessionLayer()
        if not session.permissionToEdit:
            refuse("READ_ONLY", "Session layer is not editable.")
        if all(spec.layer == session for spec in prim.GetPrimStack()):
            with Usd.EditContext(stage, session):
                if not stage.RemovePrim(p):
                    refuse("DELETE_FAILED", "Session-layer removal failed.")
            return dict(
                deleted=p, method="removed_from_session_layer", still_composed=bool(stage.GetPrimAtPath(p)),
                foreign=True, source_files_changed=False,
            )
        was_active = prim.IsActive()
        with Usd.EditContext(stage, session):
            if not prim.SetActive(False):
                refuse("DELETE_FAILED", "Session-layer deactivation failed.")
        return dict(
            deleted=p, method="deactivated_in_session_layer", already_inactive=not was_active,
            still_composed=True, active=False, foreign=True, source_files_changed=False,
            note="The prim is inactive, so rendering, physics and traversal skip it, but it still holds this "
                 "path: create a replacement at a new path. Reopening the file restores it; a sim__stage__save copy "
                 "omits it, so opening that copy frees the path.",
        )

    def set_attribute(self, stage, a):
        s = self.s
        prim = s.prim(stage, a["path"])
        if prim.IsInstanceProxy():
            refuse(
                "INSTANCE_PROXY",
                f"{a['path']} is an instance proxy inside an instance and cannot be edited directly. Edit the "
                "instance root instead.",
            )
        if prim.GetPath().GetPrefixes()[0].name in KIT_ROOTS:
            refuse("PROTECTED_PRIM", "Kit's runtime render and viewport prims are protected.")
        name = a["attribute"]
        if not isinstance(name, str) or len(name) > 128 or not ATTRIBUTE_NAME.fullmatch(name):
            refuse("INVALID_VALUE", "attribute must be a USD property name such as visibility, purposes or "
                   "user:owner (letters, digits, _ and :, at most 128 characters).")
        owned = attribute_kind(name)
        if owned:
            refuse("ENGINE_OWNED_ATTRIBUTE", f"{name} cannot be set here. {owned}")
        type_name = a.get("type")
        if type_name is not None and type_name not in SUPPORTED_TYPES:
            refuse(
                "UNSUPPORTED_TYPE",
                f"type {type_name!r} is not supported. Use one of: {', '.join(SUPPORTED_TYPES)}.",
            )
        attr = prim.GetAttribute(name) if prim.HasAttribute(name) else None
        created = attr is None
        if attr:
            type_name = str(attr.GetTypeName())
            if type_name not in SUPPORTED_TYPES:
                refuse(
                    "UNSUPPORTED_TYPE",
                    f"{name} on {a['path']} has USD type {type_name}, which this tool does not convert. "
                    f"Supported types: {', '.join(SUPPORTED_TYPES)}.",
                )
        elif not type_name:
            refuse(
                "INVALID_VALUE",
                f"{a['path']} has no attribute {name!r}. Pass type to create it, e.g. type=\"float\" or "
                "type=\"float3\"; supported types: " + ", ".join(SUPPORTED_TYPES) + ".",
            )
        value = typed_value(a["value"], type_name)
        before = attr.Get() if attr else None
        authored_before = bool(attr and attr.HasAuthoredValueOpinion())
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            s.remember_properties(stage, a["path"], [name])
            if not attr:
                attr = prim.CreateAttribute(name, Sdf.ValueTypeNames.Find(type_name), custom=True)
            if not attr or not attr.Set(value):
                refuse("SET_FAILED", f"Could not author {name} on {a['path']}; inspect the prim before retrying.")
        after = prim.GetAttribute(name).Get()
        return dict(
            path=a["path"],
            attribute=name,
            type=type_name,
            before=jsonable(before),
            after=jsonable(after),
            authored_before=authored_before,
            created_attribute=created,
            layer="session",
            source_files_changed=False,
            revert="sim__prim__revert",
        )

    def physics_push(self, stage, a):
        """A one-off impulse on a body in the running simulation: edits nothing and needs no stopped timeline."""
        from .physics import manager

        if manager().get_active_physics_engine() == "newton":
            refuse("IMPULSE_UNSUPPORTED", "Newton exposes tensor forces, not an atomic one-shot impulse. "
                   "Use sim__simulation(action='run') with a force controller and explicit duration; "
                   "no force, velocity change, or physics step was applied.", backend="newton")
        import carb
        import omni.physx
        import omni.usd
        from pxr import PhysicsSchemaTools, UsdGeom, UsdPhysics

        s = self.s
        path = a["path"]
        prim = s.prim(stage, path)
        if s.timeline.is_stopped():
            refuse("PHYSICS_NOT_READY", "An impulse acts on a running simulation: play or pause first.")
        if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
            refuse("INVALID_PHYSICS", f"{path} is not a rigid body. Push a body, for example a block or a robot link.")
        impulse, at = a["impulse"], a.get("at")
        for label, value in (("impulse", impulse), ("at", at)):
            if value is not None and (not isinstance(value, (list, tuple)) or len(value) != 3
                                      or not all(type(v) in (int, float) and math.isfinite(v) for v in value)):
                refuse("INVALID_VALUE", f"{label} must be three finite numbers.")
        if at is None:
            box = UsdGeom.BBoxCache(s.timeline.get_current_time() * stage.GetTimeCodesPerSecond(), [UsdGeom.Tokens.default_]).ComputeWorldBound(prim)
            at = list(box.ComputeAlignedRange().GetMidpoint())
        stage_id = omni.usd.get_context().get_stage_id()
        omni.physx.get_physx_simulation_interface().apply_force_at_pos(
            stage_id, PhysicsSchemaTools.sdfPathToInt(prim.GetPath()), carb.Float3(*[float(v) for v in impulse]), carb.Float3(*[float(v) for v in at]), "Impulse")
        return dict(path=path, impulse=list(impulse), at=[float(v) for v in at], unit="N s (kg m/s)",
                    backend="physx", note="One push; it takes effect on the next physics step.")

    def physics_apply(self, stage, a):
        from pxr import UsdPhysics

        if a.get("impulse") is not None:
            others = sorted(k for k, v in a.items() if v not in (None, "") and k not in ("path", "impulse", "at", "session_id", "stage_id"))  # "": a contract default
            if others:
                refuse("CONFLICT", f"impulse pushes a running body and edits nothing: it does not combine with {', '.join(others)}.")
            return self.physics_push(stage, a)
        s = self.s
        path = a["path"]
        prim = s.prim(stage, path)
        if prim.IsInstanceProxy():
            refuse(
                "INSTANCE_PROXY",
                f"{path} is an instance proxy inside an instance. Edit the instance root instead.",
            )
        if prim.GetPath().GetPrefixes()[0].name in KIT_ROOTS:
            refuse("PROTECTED_PRIM", "Kit's runtime render and viewport prims are protected.")
        collider, rigid = a.get("collider"), a.get("rigid_body")
        kinematic, approximation = a.get("kinematic"), a.get("approximation") or ""
        mass, density = a.get("mass"), a.get("density")
        for label, flag in (("collider", collider), ("rigid_body", rigid), ("kinematic", kinematic)):
            if flag is not None and type(flag) is not bool:
                refuse("INVALID_VALUE", f"{label} must be true or false.")
        if collider is None and rigid is None and kinematic is None and mass is None and density is None:
            refuse("INVALID_VALUE",
                   "Give at least one of collider, rigid_body, kinematic, mass or density. Nothing was changed.")
        if approximation and collider is not True:
            refuse("INVALID_VALUE", "approximation needs collider=true. Nothing was changed.")
        if approximation and approximation not in (
                "convexHull", "convexDecomposition", "boundingSphere", "boundingCube",
                "meshSimplification", "none"):
            refuse("INVALID_VALUE", "approximation must be convexHull, convexDecomposition, boundingSphere, "
                   "boundingCube, meshSimplification or none.")
        if mass is not None and density is not None:
            refuse("CONFLICT", "Give mass or density, not both: a mass overrides the density-derived one.")
        for label, value in (("mass", mass), ("density", density)):
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
                refuse("INVALID_VALUE", f"{label} must be a finite number greater than 0.")
        if rigid is True:
            if prim.IsA(UsdPhysics.Joint) or prim.HasAPI(UsdPhysics.ArticulationRootAPI):
                refuse(
                    "INVALID_PHYSICS",
                    f"{path} is a joint or articulation root, not a link; a rigid body belongs on the link "
                    "prims under it. sim__physics__inspect lists them.",
                )
            ancestor = prim.GetParent()
            while ancestor and not ancestor.IsPseudoRoot():
                if ancestor.HasAPI(UsdPhysics.RigidBodyAPI):
                    refuse(
                        "NESTED_BODY",
                        f"{ancestor.GetPath()} already carries a rigid body, and a rigid body under another one "
                        "is not a separate simulation body. Apply it to a link that has no body ancestor.",
                        body_ancestor=str(ancestor.GetPath()),
                    )
                ancestor = ancestor.GetParent()
        has_body = prim.HasAPI(UsdPhysics.RigidBodyAPI)
        if kinematic is not None and not (rigid is True or (rigid is None and has_body)):
            refuse("INVALID_PHYSICS",
                   "kinematic needs a rigid body on this prim: pass rigid_body=true with it, or apply the body first.")
        if rigid is False and kinematic is True:
            refuse("CONFLICT", "A removed rigid body cannot be kinematic. Nothing was changed.")
        before = s.get(stage, path)
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            if collider is True:
                s.remember_api(stage, path, ["PhysicsCollisionAPI"])
                UsdPhysics.CollisionAPI.Apply(prim)
                if approximation:
                    s.remember_api(stage, path, ["PhysicsMeshCollisionAPI"])
                    s.remember_properties(stage, path, ["physics:approximation"])
                    UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr(approximation)
            elif collider is False:
                remove_api_schema(stage, prim, "PhysicsCollisionAPI", "collider=true")
            if rigid is True:
                s.remember_api(stage, path, ["PhysicsRigidBodyAPI"])
                UsdPhysics.RigidBodyAPI.Apply(prim)
            elif rigid is False:
                remove_api_schema(stage, prim, "PhysicsRigidBodyAPI", "rigid_body=true")
            if kinematic is not None:
                s.remember_properties(stage, path, ["physics:kinematicEnabled"])
                UsdPhysics.RigidBodyAPI(prim).CreateKinematicEnabledAttr(kinematic)
            if mass is not None or density is not None:
                s.remember_api(stage, path, ["PhysicsMassAPI"])
                mass_api = UsdPhysics.MassAPI.Apply(prim)
                if mass is not None:
                    s.remember_properties(stage, path, ["physics:mass"])
                    mass_api.CreateMassAttr(float(mass))
                else:
                    s.remember_properties(stage, path, ["physics:density"])
                    mass_api.CreateDensityAttr(float(density))
        after = s.get(stage, path)
        scale = UsdPhysics.GetStageKilogramsPerUnit(stage)
        result = dict(
            path=path,
            before=before,
            after=after,
            applied=dict(
                collider=collider, approximation=approximation or None, rigid_body=rigid,
                kinematic=kinematic, mass=mass, density=density,
            ),
            units=dict(mass="kilograms (stage mass units)", density="kg/m^3, scaled by the stage units"),
            stage_kilograms_per_unit=scale,
            layer="session",
            source_files_changed=False,
            revert="sim__prim__revert",
        )
        mass_value = after["physics"]["mass"]
        if mass_value is not None:
            result["authored_mass_kg"] = mass_value * scale
        if (mass is not None or density is not None) and not after["physics"]["rigid_body"]:
            result["mass_note"] = ("Authored, but this prim is not a rigid body (and has no body ancestor), so the "
                                   "value has no runtime effect yet. Apply rigid_body=true for simulation.")
        if collider is True and not prim.IsA(UsdGeom.Gprim):
            result["collision_note"] = ("CollisionAPI is authored, but this prim is not geometry; the physics "
                                        "backend simulates a collider only on geometry such as a mesh or shape.")
        return result

    def revert(self, stage, a):
        s = self.s
        subtree = a.get("subtree", True)
        if type(subtree) is not bool:
            refuse("INVALID_VALUE", "subtree must be true or false.")
        s.prim(stage, a["path"])
        result = s.revert(stage, a["path"], subtree)
        if not result["reverted_paths"]:
            result["note"] = ("No session-layer opinion from these tools is recorded on this prim or its subtree, so "
                              "nothing was removed. Edits recorded before a stage or session change are not "
                              "recoverable; the source files were never changed.")
        return result

    def tool_prim_bounds(self, name, stage, a, camera_frame=None):
        from . import geometry

        if name == "sim.prim.bounds":
            return geometry.world_bound(stage, a["path"], a.get("purpose", "default"))
        return geometry.measure(stage, a["path_a"], a["path_b"])

    def tool_physics_raycast(self, name, stage, a, camera_frame=None):
        s = self.s
        from . import queries

        if name == "sim.physics.raycast":
            return queries.raycast(
                a["origin"], a["direction"], a.get("max_distance", 100),
                a.get("all", False), timeline=s.timeline,
            )
        return queries.contacts(a["paths"], stage, timeline=s.timeline)

    def tool_prim_delete(self, name, stage, a, camera_frame=None):
        s = self.s
        if a["confirm"] is not True:
            refuse("CONFIRM_REQUIRED", "confirm=true required.")
        p = a["path"]
        allow_foreign = a.get("allow_foreign", False)
        if type(allow_foreign) is not bool:
            refuse("INVALID_VALUE", "allow_foreign must be boolean.")
        foreign = p not in s.owned
        direct_sensor = (self.sensors[p][1].get("sensor_path") or p) if p in self.sensors else False
        if foreign:
            from .sensors import find_sensor_prim

            prim = foreign_prim(s, stage, p, allow_foreign)
            if not direct_sensor:
                # An unregistered sensor holds no resources, so this exact prim may go.
                found = find_sensor_prim(stage, p, "lidar") or find_sensor_prim(stage, p, "camera")
                direct_sensor = str(found.GetPath()) if found else False
        deletable(
            s, stage, p, direct_sensor=direct_sensor, sensor_paths=self.sensors, foreign=foreign
        )
        if p in self.sensors:
            from .sensors import dispose

            errors = dispose(self.sensors[p][1])
            if errors:
                refuse(
                    "SENSOR_CLEANUP_FAILED",
                    "Some resources may have been released; inspect before retrying: "
                    + str(errors),
                )
            del self.sensors[p]
        if foreign:
            return self.delete_foreign(stage, prim)
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            if not stage.RemovePrim(p):
                refuse("DELETE_FAILED", "Session-layer removal failed.")
        s.owned = {q for q in s.owned if q != p and not q.startswith(p + "/")}
        s.bulk_owned = {q for q in getattr(s, "bulk_owned", ()) if q != p and not q.startswith(p + "/")}
        return dict(deleted=p, still_composed=bool(stage.GetPrimAtPath(p)))

    def tool_stage_clear_owned(self, name, stage, a, camera_frame=None):
        s = self.s
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

    def tool_prim_clone(self, name, stage, a, camera_frame=None):
        s = self.s
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

    def tool_light(self, name, stage, a, camera_frame=None):
        s = self.s
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

    def tool_material_create(self, name, stage, a, camera_frame=None):
        s = self.s
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

    def tool_material_apply(self, name, stage, a, camera_frame=None):
        s = self.s
        paths, single = a.get("paths"), a.get("path")
        if (paths is None) == (single in (None, "")):
            refuse("INVALID_VALUE", "Give path (one prim) or paths (a list of 1 to 128 prims), one of the two.")
        if paths is None:
            return bind_material(s, stage, single, a["material_path"])
        if not isinstance(paths, list) or not 1 <= len(paths) <= 128:
            refuse("INVALID_VALUE", "paths must be a list of 1 to 128 prim paths.")
        owned(s, stage, a["material_path"])
        for p in paths:  # check every prim before binding any, so a bad one leaves nothing half done
            if not isinstance(p, str) or not UsdGeom.Gprim(s.prim(stage, p)):
                refuse("WRONG_TYPE", f"{p!r} is not geometry, so nothing was bound.", failed_path=p)
        return dict(applied=[bind_material(s, stage, p, a["material_path"]) for p in paths])

    def tool_prim_create_many(self, name, stage, a, camera_frame=None):
        s = self.s
        return create_many(s, stage, a)

    def tool_physics_configure(self, name, stage, a, camera_frame=None):
        s = self.s
        sm = self.physics_manager()
        prim = s.prim(stage, a["path"])
        physics = UsdPhysics.Scene(prim)
        if not physics:
            refuse("WRONG_TYPE", "Not a PhysicsScene.")
        gravity = vector(a.get("gravity", [0, 0, -9.81]), 3)
        dt = scalar(a.get("dt", 1 / 60), 0.001, 0.1)
        engine = sm.get_active_physics_engine()
        buffer_config, buffer_report = None, None
        if engine == "newton":
            from .newton_support import prepare_buffers

            buffer_config, buffer_report = prepare_buffers(stage, prim, s.timeline, a.get("nconmax"), a.get("njmax"))
        elif any(a.get(key) is not None for key in ("nconmax", "njmax")):
            refuse("NEWTON_CONFIG_UNSUPPORTED", "nconmax/njmax are Newton MuJoCo settings, not PhysX settings.")
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
                from .newton_support import scene_settings

                scene_settings(prim, rate)
        result = dict(path=a["path"], gravity=gravity, dt=1 / rate, requested_dt=dt)
        if buffer_config is not None:
            from .newton_support import apply_buffers

            apply_buffers(buffer_config)
            result["newton_buffers"] = buffer_report
        return result

    def tool_prim_set_attribute(self, name, stage, a, camera_frame=None):
        return self.set_attribute(stage, a)

    def tool_physics_apply(self, name, stage, a, camera_frame=None):
        return self.physics_apply(stage, a)

    def tool_prim_revert(self, name, stage, a, camera_frame=None):
        return self.revert(stage, a)
