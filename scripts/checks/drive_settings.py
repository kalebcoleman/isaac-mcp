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
# The twin writes its gains verbatim into USD drive attributes, whose angular units are per DEGREE (N*m/deg).
# Per radian they are 57.3x larger: 100 N*m/deg = 5729.6 N*m/rad.
nominal = {'stiffness_usd': 100.0, 'damping_usd': 10.0, 'max_force': 200,
           'stiffness_per_rad': 100 * 180 / math.pi, 'damping_per_rad': 10 * 180 / math.pi}
result = {'nominal_from_twin_params': nominal, 'live_arm_drives': rows}
