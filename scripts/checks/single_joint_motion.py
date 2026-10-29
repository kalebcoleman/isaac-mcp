import time
import numpy as np
import omni.kit.app, omni.timeline
from isaacsim.core.prims import Articulation
from isaacsim.core.utils.types import ArticulationActions
timeline = omni.timeline.get_timeline_interface()
app = omni.kit.app.get_app()
timeline.stop(); timeline.set_current_time(0); timeline.play()
for _ in range(10):
    app.update()
art = Articulation('/World/UR5e', name=f'verify_motion_{int(time.time()*1000)}')
art.initialize()
j = art.dof_names.index('elbow_joint')
settled = art.get_joint_positions()[0].copy()
def run_to(seconds, target):
    start = timeline.get_current_time()
    while timeline.get_current_time() - start < seconds:
        art.apply_action(ArticulationActions(joint_positions=np.array([target])))
        app.update()
run_to(1.0, settled)
# Move up, away from the tabletop collider at z=0. A +0.1 rad elbow move drives the forearm into the table.
moved = settled.copy(); moved[j] -= 0.1
run_to(3.0, moved)
achieved = art.get_joint_positions()[0].copy()
run_to(3.0, settled)
returned = art.get_joint_positions()[0].copy()
timeline.pause()
result = {'joint': 'elbow_joint', 'commanded_rad': -0.1,
          'achieved_rad': float(achieved[j] - settled[j]),
          'returned_error_rad': float(returned[j] - settled[j]),
          'all_joints_finite': bool(np.all(np.isfinite(achieved)))}
