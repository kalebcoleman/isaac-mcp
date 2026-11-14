"""A room around the work cell: an NVIDIA Isaac Sim environment, referenced (never copied into a pack).

The room is moved so its ground plane is level with the bottom of the bench. Room parts that overlap the
bench are deactivated (Simple Room's own table stands where the bench goes), and every room collider except the
ground plane is switched off, so the room adds one static floor to the physics and nothing else: a physics scene
the room brings is deactivated (the cell has its own), its joints are deactivated and articulation roots removed,
its rigid bodies are made kinematic, instanced parts are de-instanced so their colliders can be switched off, and
its light prims are deactivated so renders keep the cell's lighting (an emissive mesh keeps its geometry).
"""

ENVIRONMENTS = {"simple_room": "Isaac/Environments/Simple_Room/simple_room.usd"}
ROOT = "/World/Environment"
NAMES = ("none",) + tuple(ENVIRONMENTS)
OVERLAP_M = 0.01      # a room part must reach this far into the bench's box to count as overlapping
MAX_INSTANCE_DEPTH = 8


def resolve(name, assets_root=None, reachable=None):
    """The room's URL for ``name``, checked before any stage is replaced; None for "none"."""
    if name in (None, "none"):
        return None
    if name not in ENVIRONMENTS:
        raise ValueError(f"environment must be one of {list(NAMES)}, not {name!r}")
    if assets_root is None:
        from isaacsim.storage.native import get_assets_root_path
        assets_root = get_assets_root_path()
    if not assets_root:
        raise RuntimeError(f"Isaac Sim reports no NVIDIA assets root, so the {name} environment cannot be loaded; "
                           "quick_start without environment works offline.")
    url = assets_root.rstrip("/") + "/" + ENVIRONMENTS[name]
    if reachable is None:
        import omni.client

        def reachable(path):
            return omni.client.stat(path)[0] == omni.client.Result.OK
    if not reachable(url):
        raise RuntimeError(f"The {name} environment is not reachable at {url}, so no stage was replaced; "
                           "quick_start without environment works offline.")
    return url


def _box(cache, prim):
    box = cache.ComputeWorldBound(prim).ComputeAlignedRange()
    return None if box.IsEmpty() else box


def _overlaps(a, b, margin=OVERLAP_M):
    return all(min(a.GetMax()[i], b.GetMax()[i]) - max(a.GetMin()[i], b.GetMin()[i]) > margin for i in range(3))


def place(stage, name, url, bench_path="/World/Bench"):
    """Reference the room under ROOT, level its ground with the bench's feet, deactivate room parts inside the
    bench's box and switch off every room collider but the ground plane. Returns what was done."""
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    bench = _box(cache, stage.GetPrimAtPath(bench_path))
    if bench is None:
        raise ValueError(f"{bench_path} has no geometry to stand the room around")
    holder = UsdGeom.Xform.Define(stage, ROOT)
    holder.ClearXformOpOrder()
    offset = holder.AddTranslateOp()
    offset.Set(Gf.Vec3d(0, 0, 0))
    room = stage.DefinePrim(f"{ROOT}/{name}", "Xform")
    room.GetReferences().AddReference(url)
    from pxr import UsdLux

    deinstanced = 0
    for _ in range(MAX_INSTANCE_DEPTH + 1):   # instance proxies cannot be edited: open instanced parts, outermost first
        instanced = [prim for prim in Usd.PrimRange(room) if prim.IsInstanceable()]
        if not instanced:
            break
        for prim in instanced:
            prim.SetInstanceable(False)
        if any(prim.IsInstanceable() for prim in instanced):
            raise ValueError(f"The {name} environment keeps instanced parts open to no edit (a stronger layer sets "
                             "instanceable); its colliders cannot be switched off")
        deinstanced += len(instanced)
    else:
        raise ValueError(f"The {name} environment nests instances more than {MAX_INSTANCE_DEPTH} deep")
    scenes, lights, joints, articulations = [], [], [], []
    for prim in Usd.PrimRange(room):
        if prim.IsA(UsdPhysics.Scene):
            scenes.append(prim)
        elif prim.IsA(UsdLux.BoundableLightBase) or prim.IsA(UsdLux.NonboundableLightBase):
            lights.append(prim)        # light prims only: a mesh with MeshLightAPI keeps its geometry and collider
        elif prim.IsA(UsdPhysics.Joint):
            joints.append(prim)        # a hinged prop: a kinematic link cannot stay jointed on PhysX
        if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
            articulations.append(prim)
    for prim in scenes + lights + joints:
        prim.SetActive(False)
    for prim in articulations:
        prim.RemoveAPI(UsdPhysics.ArticulationRootAPI)
    kinematic = 0
    for prim in Usd.PrimRange(room):
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            UsdPhysics.RigidBodyAPI(prim).CreateKinematicEnabledAttr().Set(True)
            kinematic += 1
    planes = [prim for prim in Usd.PrimRange(room)
              if prim.GetTypeName() == "Plane" and prim.HasAPI(UsdPhysics.CollisionAPI)]
    if planes:
        ground_z = UsdGeom.Xformable(planes[0]).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation()[2]
    else:
        whole = _box(cache, room)
        if whole is None:
            raise ValueError(f"The {name} environment at {url} has no geometry")
        ground_z = whole.GetMin()[2]
    lift = bench.GetMin()[2] - ground_z
    offset.Set(Gf.Vec3d(0, 0, lift))
    cache.Clear()
    keep = {str(plane.GetPath()) for plane in planes[:1]}
    removed = []
    for child in room.GetChildren():
        box = _box(cache, child)
        holds_ground = any(path == str(child.GetPath()) or path.startswith(str(child.GetPath()) + "/") for path in keep)
        if box is not None and not holds_ground and _overlaps(box, bench):
            child.SetActive(False)
            removed.append(child.GetName())
    switched_off = 0
    for prim in Usd.PrimRange(room):
        if prim.HasAPI(UsdPhysics.CollisionAPI) and str(prim.GetPath()) not in keep:
            UsdPhysics.CollisionAPI(prim).CreateCollisionEnabledAttr().Set(False)
            switched_off += 1
    return {"name": name, "path": str(room.GetPath()), "url": url, "floor_z_m": round(bench.GetMin()[2], 4),
            "lifted_m": round(lift, 4), "deactivated_for_overlap": removed,
            "colliders": {"ground_plane": sorted(keep)[0] if keep else None, "switched_off": switched_off},
            "physics_scenes_deactivated": len(scenes), "rigid_bodies_made_kinematic": kinematic,
            "deinstanced": deinstanced, "lights_deactivated": len(lights), "joints_deactivated": len(joints),
            "articulation_roots_removed": len(articulations)}
