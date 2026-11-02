"""Observe authored physics and the initialized timestep, without authored edits."""
import omni.usd
import omni.timeline
from pxr import UsdPhysics
from isaacsim.core.simulation_manager import SimulationManager

stage = omni.usd.get_context().get_stage()
scenes = [p for p in stage.TraverseAll() if p.IsA(UsdPhysics.Scene)]
initialized = not omni.timeline.get_timeline_interface().is_stopped()
result = {
    'scenes': [dict(path=str(p.GetPath()), type=p.GetTypeName(),
                    gpu_dynamics=p.GetAttribute('physxScene:enableGPUDynamics').Get(),
                    broadphase=p.GetAttribute('physxScene:broadphaseType').Get(),
                    steps_per_second=p.GetAttribute('physxScene:timeStepsPerSecond').Get()) for p in scenes],
    'initialized': initialized,
    'runtime_dt': SimulationManager.get_physics_dt() if initialized else None,
}
