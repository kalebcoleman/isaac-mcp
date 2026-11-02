"""Validate private native URDF output before snapshotting or composing it.

Some 6.x importers author Physics=physx while offering only physics/none;
others leave the selection empty. A successful conversion then has geometry
but no composed mass, bodies or joints. Select the shared physics description
on our generated derivative only. This is not a runtime physics qualification.
"""

from pathlib import Path

from pxr import Usd, UsdPhysics

from isaac_mcp.project_assets import refuse


def prepare_generated_physics(filename, output_root, movable_joints=0):
    filename, output_root = Path(filename), Path(output_root)
    try:
        resolved = filename.resolve(strict=True)
        resolved.relative_to(output_root.resolve(strict=True))
    except (OSError, ValueError):
        refuse(
            "URDF_OUTPUT", "Native converter output is outside its private directory."
        )
    if filename.is_symlink() or not resolved.is_file():
        refuse(
            "URDF_OUTPUT", "Native converter must return a regular generated USD file."
        )
    asset = Usd.Stage.Open(str(resolved), load=Usd.Stage.LoadAll)
    if not asset or not asset.GetDefaultPrim():
        refuse("URDF_PHYSICS", "Converted URDF has no valid default prim.")
    prim = asset.GetDefaultPrim()
    variants = prim.GetVariantSets()
    variant = (
        variants.GetVariantSet("Physics") if variants.HasVariantSet("Physics") else None
    )
    selected_before = variant.GetVariantSelection() if variant else None
    names = variant.GetVariantNames() if variant else []
    if variant:
        if len(names) > 64 or "physics" not in names:
            refuse(
                "URDF_PHYSICS",
                "Converted URDF has no shared 'physics' variant; no backend-specific or geometry-only fallback.",
                available_physics_variants=names[:64],
            )
        # Validate in this isolated stage's session first, without changing files.
        with Usd.EditContext(asset, asset.GetSessionLayer()):
            if not variant.SetVariantSelection("physics"):
                refuse("URDF_PHYSICS", "Cannot compose the converted physics variant.")
    errors = asset.GetCompositionErrors()
    if errors:
        refuse(
            "URDF_PHYSICS",
            "Converted physics has composition errors: " + str(errors)[:1200],
        )
    counts = dict(
        rigid_bodies=0,
        collisions=0,
        joints=0,
        movable_joints=0,
        articulations=0,
        authored_masses=0,
    )
    for count, node in enumerate(Usd.PrimRange(prim, Usd.TraverseInstanceProxies()), 1):
        if count > 10000:
            refuse("LIMIT", "Converted URDF exceeds 10000 composed prims.")
        counts["rigid_bodies"] += int(node.HasAPI(UsdPhysics.RigidBodyAPI))
        counts["collisions"] += int(node.HasAPI(UsdPhysics.CollisionAPI))
        counts["joints"] += int(node.IsA(UsdPhysics.Joint))
        counts["movable_joints"] += int(
            node.IsA(UsdPhysics.RevoluteJoint) or node.IsA(UsdPhysics.PrismaticJoint)
        )
        counts["articulations"] += int(node.HasAPI(UsdPhysics.ArticulationRootAPI))
        mass = node.GetAttribute("physics:mass")
        counts["authored_masses"] += int(bool(mass and mass.HasAuthoredValueOpinion()))
    if not counts["rigid_bodies"] or counts["movable_joints"] < movable_joints:
        refuse(
            "URDF_PHYSICS",
            "Converted URDF lacks expected rigid bodies or revolute/prismatic joints; active stage was not imported.",
            authored_physics=counts,
            expected_movable_joints=movable_joints,
        )
    if variant:
        # Only the service-owned generated interface is written. Input URDF,
        # dependencies, native physics layers and the selected Kit stage stay intact.
        with Usd.EditContext(asset, asset.GetRootLayer()):
            if (
                not variant.SetVariantSelection("physics")
                or not asset.GetRootLayer().Save()
            ):
                refuse(
                    "URDF_PHYSICS",
                    "Cannot persist the private generated physics selection.",
                )
    return dict(
        variant_set="Physics" if variant else None,
        selected_before=selected_before,
        selected="physics" if variant else None,
        available=names,
        authored=counts,
        runtime_readiness="unknown; initialize and observe on the selected backend",
    )
