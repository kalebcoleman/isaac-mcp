import math
import omni.usd
from pxr import UsdPhysics
stage = omni.usd.get_context().get_stage()
rows = {}
for name in ['shoulder_pan_joint', 'shoulder_lift_joint', 'elbow_joint', 'wrist_1_joint', 'wrist_2_joint', 'wrist_3_joint']:
    prim = stage.GetPrimAtPath(f'/World/UR5e/joints/{name}')
    if not prim:
        rows[name] = 'missing'
        continue
    drive = UsdPhysics.DriveAPI(prim, 'angular')
    rows[name] = {'stiffness': drive.GetStiffnessAttr().Get(), 'damping': drive.GetDampingAttr().Get(),
                  'max_force': prim.GetAttribute('drive:angular:physics:maxForce').Get()}
nominal = {'stiffness_usd': 100 * math.pi / 180, 'damping_usd': 10 * math.pi / 180, 'max_force': 200}
result = {'nominal_from_twin_params': nominal, 'live_arm_drives': rows}
