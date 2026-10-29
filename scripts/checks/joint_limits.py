import omni.usd
from pxr import UsdPhysics
stage = omni.usd.get_context().get_stage()
result = {}
for name in ['shoulder_pan_joint', 'shoulder_lift_joint', 'elbow_joint', 'wrist_1_joint', 'wrist_2_joint', 'wrist_3_joint']:
    joint = UsdPhysics.RevoluteJoint(stage.GetPrimAtPath(f'/World/UR5e/joints/{name}'))
    result[name] = {'lower_deg': joint.GetLowerLimitAttr().Get(), 'upper_deg': joint.GetUpperLimitAttr().Get()}
