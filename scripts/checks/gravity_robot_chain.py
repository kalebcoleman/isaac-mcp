import omni.usd
from pxr import UsdPhysics, PhysxSchema
stage = omni.usd.get_context().get_stage()
roots = ['/World/UR5e', '/World/RG2_Gripper', '/World/QuickChanger']
result = {}
for root in roots:
    bodies = [p for p in stage.Traverse() if str(p.GetPath()).startswith(root + '/') and p.HasAPI(UsdPhysics.RigidBodyAPI)]
    enabled = []
    for p in bodies:
        if p.HasAPI(PhysxSchema.PhysxRigidBodyAPI):
            value = PhysxSchema.PhysxRigidBodyAPI(p).GetDisableGravityAttr().Get()
        else:
            value = None
        if value is not True:
            enabled.append(str(p.GetPath()))
    result[root] = {'rigid_bodies': len(bodies), 'gravity_enabled': enabled[:10], 'gravity_enabled_count': len(enabled)}
