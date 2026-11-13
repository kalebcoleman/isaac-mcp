"""A giant Jenga tower on a table with a Franka beside it, for ``sim__quick_start environment="jenga"``.

Plain USD boxes, authored in the session layer under ``/World/Jenga``: nothing is downloaded. Blocks are twice real size
(15 x 5 x 3 cm, 120 g), 18 layers of 3, 54 blocks. Z up, metres. Odd layers run along x, so the middle block of an odd layer can be
pushed out by the arm from the table's -x end.

Two choices come from the Franka itself. Its palm is 20 cm wide and its fingers reach only about 4.5 cm past the palm, so a deep push
would drive the palm into the tower: the hand holds a thin push rod between open fingers. Its wrist limit (joint 6) only lets the hand
stay level about 60 cm or more above the base, so the arm stands on the floor beside a low table, with layer 9 at that height.
"""

from __future__ import annotations

import math

from pxr import Gf, PhysxSchema, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

ROOT = "/World/Jenga"
TABLE_TOP = 0.34
TABLE_CENTER, TABLE_SIZE = (0.65, 0.0), (0.9, 1.0)
TOWER_XY = (0.7, 0.0)
BLOCK = (0.15, 0.05, 0.03)  # length, width, height
BLOCK_MASS = 0.12
LAYERS = 18
SIDE_GAP = 0.0005  # between the blocks of a layer: a real tower's blocks are not jammed together
LOOSE = 0.0004  # the middle block of an odd layer is this much thinner, so the layer above rests on its sides. Blocks of one height leave the
# split of the load to the solver: on Sim 5.1 it put it on the middle block, and pushing that carried the whole upper tower with it.
FRICTION = (0.5, 0.4)  # static and dynamic, about wood on wood
ROBOT_BASE = (0.0, 0.0, 0.0)
READY_POSE = dict(panda_joint1=0.0, panda_joint2=-0.8758, panda_joint3=0.0, panda_joint4=-2.8161, panda_joint5=0.0, panda_joint6=3.511, panda_joint7=0.785,
                  panda_finger_joint1=0.02, panda_finger_joint2=0.02)  # hand level at layer 9, the rod's tip 6 cm from the tower, fingers around the rod
HAND_LINK = "panda_hand"
ROD_WIDTH = 0.012
ROD_FROM, ROD_TIP = 0.05, 0.26  # along the hand's z axis, from inside the palm to 15 cm past the fingertips
HAND_LEVEL = [0.0, 0.7071068, 0.0, 0.7071068]  # wxyz: hand z along world +x, as the ready pose holds it (joint 7 unturned)
COLORS = ((0.80, 0.65, 0.43), (0.76, 0.60, 0.38), (0.84, 0.70, 0.48))
VIEW = (1.75, -1.35, 1.1), (0.45, 0.0, 0.55)


def _is_loose(layer: int, index: int) -> bool:
    return layer % 2 == 1 and index == 1


def block_centers():
    """``(layer, index, along_x, x, y, z)`` for each block, bottom layer first."""
    length, width, height = BLOCK
    step = width + SIDE_GAP
    for layer in range(LAYERS):
        along_x = layer % 2 == 1
        z = TABLE_TOP + (layer + 0.5) * height
        for index in range(3):
            offset = (index - 1) * step
            x, y = (TOWER_XY[0], TOWER_XY[1] + offset) if along_x else (TOWER_XY[0] + offset, TOWER_XY[1])
            yield layer, index, along_x, x, y, z - (LOOSE / 2 if _is_loose(layer, index) else 0.0)


def block_path(layer: int, index: int) -> str:
    return f"{ROOT}/Tower/L{layer:02d}_B{index}"


def poke_targets(layer: int = 9):
    """Where the rod's tip goes to push the middle block of *layer* (an odd layer runs along x) out of the tower, and the sim__arm__ik arguments for it."""
    if layer % 2 == 0:
        raise ValueError("layers that run along x are the odd ones")
    z = TABLE_TOP + (layer + 0.5) * BLOCK[2]
    near_face = TOWER_XY[0] - BLOCK[0] / 2
    return dict(block=block_path(layer, 1), layer=layer, approach=[near_face - 0.04, TOWER_XY[1], z], touch=[near_face, TOWER_XY[1], z],
                through=[near_face + 0.12, TOWER_XY[1], z], push_axis=[1.0, 0.0, 0.0],
                ik=dict(link=HAND_LINK, offset=[0.0, 0.0, ROD_TIP], orientation=HAND_LEVEL))


def build(stage, backend="physx", timeline=None) -> dict:
    """Author the table and the tower; returns what was built."""
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        UsdGeom.Xform.Define(stage, ROOT)
        material = UsdShade.Material.Define(stage, f"{ROOT}/BlockMaterial")
        physics = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
        physics.CreateStaticFrictionAttr(FRICTION[0])
        physics.CreateDynamicFrictionAttr(FRICTION[1])
        physics.CreateRestitutionAttr(0.0)
        table = UsdGeom.Cube.Define(stage, f"{ROOT}/Table")
        table.CreateSizeAttr(1.0)
        xform = UsdGeom.Xformable(table)
        xform.AddTranslateOp().Set(Gf.Vec3d(TABLE_CENTER[0], TABLE_CENTER[1], TABLE_TOP / 2))
        xform.AddScaleOp().Set(Gf.Vec3f(TABLE_SIZE[0], TABLE_SIZE[1], TABLE_TOP))
        table.CreateDisplayColorAttr([Gf.Vec3f(0.3, 0.32, 0.36)])
        UsdPhysics.CollisionAPI.Apply(table.GetPrim())
        if backend == "newton":
            from .newton_support import collision_settings

            collision_settings(table.GetPrim())
        UsdShade.MaterialBindingAPI.Apply(table.GetPrim()).Bind(material, UsdShade.Tokens.weakerThanDescendants, "physics")
        UsdGeom.Xform.Define(stage, f"{ROOT}/Tower")
        for layer, index, along_x, x, y, z in block_centers():
            cube = UsdGeom.Cube.Define(stage, block_path(layer, index))
            cube.CreateSizeAttr(1.0)
            xform = UsdGeom.Xformable(cube)
            xform.AddTranslateOp().Set(Gf.Vec3d(x, y, z))
            height = BLOCK[2] - (LOOSE if _is_loose(layer, index) else 0.0)
            size = (BLOCK[0], BLOCK[1], height) if along_x else (BLOCK[1], BLOCK[0], height)
            xform.AddScaleOp().Set(Gf.Vec3f(*size))
            cube.CreateDisplayColorAttr([Gf.Vec3f(*COLORS[(layer + index) % len(COLORS)])])
            prim = cube.GetPrim()
            UsdPhysics.CollisionAPI.Apply(prim)
            UsdPhysics.RigidBodyAPI.Apply(prim)
            UsdPhysics.MassAPI.Apply(prim).CreateMassAttr(BLOCK_MASS)
            UsdShade.MaterialBindingAPI.Apply(prim).Bind(material, UsdShade.Tokens.weakerThanDescendants, "physics")
            body = PhysxSchema.PhysxRigidBodyAPI.Apply(prim)  # a stack settles only with enough solver work and no early sleep jitter
            body.CreateSolverPositionIterationCountAttr(32)
            body.CreateSolverVelocityIterationCountAttr(4)
            collision = PhysxSchema.PhysxCollisionAPI.Apply(prim)
            collision.CreateContactOffsetAttr(0.004)
            collision.CreateRestOffsetAttr(0.0)
            if backend == "newton":
                collision_settings(prim)
        scene = stage.GetPrimAtPath("/World/PhysicsScene")
        if scene:
            PhysxSchema.PhysxSceneAPI.Apply(scene).CreateTimeStepsPerSecondAttr(120)
        newton = {}
        if backend == "newton":
            from .newton_support import apply_buffers, prepare_buffers, scene_settings

            config, buffers = prepare_buffers(stage, scene, timeline)
            newton = dict(newton_settings=scene_settings(scene, 120, tower=True), newton_buffers=buffers)
            apply_buffers(config)
    top = TABLE_TOP + LAYERS * BLOCK[2]
    return dict(prim=ROOT, blocks=LAYERS * 3, layers=LAYERS, block_size_m=list(BLOCK), block_mass_kg=BLOCK_MASS, table_top_z=TABLE_TOP,
                tower_xy=list(TOWER_XY), tower_top_z=round(top, 4), top_block=block_path(LAYERS - 1, 1), robot_base=list(ROBOT_BASE),
                hand_link=HAND_LINK, poke_example=poke_targets(), physics_hz=120, **newton,
                note="Blocks are twice real size so a fingertip fits one layer; Newton tuning requires live stability qualification.")


def _add_rod(stage, hand):
    """A thin box collider on the hand's own rigid body, so it moves with the hand and adds no joint."""
    rod = UsdGeom.Cube.Define(stage, f"{hand.GetPath()}/PushRod")
    rod.CreateSizeAttr(1.0)
    xform = UsdGeom.Xformable(rod)
    xform.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, (ROD_FROM + ROD_TIP) / 2))
    xform.AddScaleOp().Set(Gf.Vec3f(ROD_WIDTH, ROD_WIDTH, ROD_TIP - ROD_FROM))
    rod.CreateDisplayColorAttr([Gf.Vec3f(0.85, 0.2, 0.15)])
    UsdPhysics.CollisionAPI.Apply(rod.GetPrim())
    collision = PhysxSchema.PhysxCollisionAPI.Apply(rod.GetPrim())
    collision.CreateContactOffsetAttr(0.004)
    collision.CreateRestOffsetAttr(0.0)
    return str(rod.GetPath())


def pose_robot(stage, robot_path) -> dict:
    """Put a Franka beside the table in a ready pose, away from the tower, with both fingers driven, around a push rod.

    The joints start there rather than swinging there on Play (which could knock the tower). The second finger has no drive in NVIDIA's
    asset, so a finger command would leave it loose: it gets the same drive as the first. Other robots are placed and left as they are.
    """
    posed, fingers, rod = [], [], None
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for node in Usd.PrimRange(stage.GetPrimAtPath(robot_path), Usd.TraverseInstanceProxies()):
            name = node.GetName()
            if name == HAND_LINK and node.HasAPI(UsdPhysics.RigidBodyAPI):
                rod = _add_rod(stage, node)
                continue
            if name not in READY_POSE:
                continue
            value = READY_POSE[name]
            angular = node.IsA(UsdPhysics.RevoluteJoint)
            token = "angular" if angular else "linear"
            target = math.degrees(value) if angular else value
            drive = UsdPhysics.DriveAPI.Apply(node, token)
            drive.CreateTargetPositionAttr(target)
            state = PhysxSchema.JointStateAPI.Apply(node, token)
            state.CreatePositionAttr(target)
            state.CreateVelocityAttr(0.0)
            posed.append(name)
            if "finger" in name:
                fingers.append(name)
                drive.CreateTypeAttr("force")
                drive.CreateStiffnessAttr(2000.0)
                drive.CreateDampingAttr(100.0)
                drive.CreateMaxForceAttr(100.0)
    return dict(posed_joints=posed, fingers=fingers, ready_pose=READY_POSE if posed else None, push_rod=rod)
