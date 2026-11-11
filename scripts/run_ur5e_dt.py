"""Start Isaac Sim with a visible window and keep it running until closed.

Kit extension arguments are passed through by run.sh. The app stays up so
the Kit MCP bridge can be driven from another terminal (isaac_mcp.client).
"""
import sys

from isaacsim import SimulationApp

import os

app = SimulationApp({'headless': os.getenv('UR5E_DT_HEADLESS') == '1', 'width': 1280, 'height': 720})
# Select Newton after the base application has initialized its viewport. This
# avoids the older engine-switch/startup interaction while keeping the new
# managed session empty. Never change an existing application or fall back.
backend = os.getenv('ISAAC_MCP_PHYSICS_BACKEND', 'physx')
if backend not in ('physx', 'newton'):
    raise RuntimeError('ISAAC_MCP_PHYSICS_BACKEND must be physx or newton')
if backend == 'newton':
    import omni.kit.app
    if not omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate('isaacsim.physics.newton', True):
        raise RuntimeError('Requested Newton extension could not be enabled; no PhysX fallback')
    from isaacsim.core.simulation_manager import SimulationManager
    if SimulationManager.get_active_physics_engine() != 'newton':
        raise RuntimeError('Requested Newton engine is not active; no PhysX fallback')
    # Releases after 6.0.0.0 ship Newton's tensor views as a separate extension that the base app
    # leaves off. Without it the first Play has no body or joint state to read, so a Newton session
    # is not ready until it is on. Enabled here, at launch, so that holds whoever presses Play.
    manager = omni.kit.app.get_app().get_extension_manager()
    tensors = 'isaacsim.physics.newton.tensors'
    if any(entry.get('name') == tensors or str(entry.get('id', '')).startswith(tensors + '-') for entry in manager.get_extensions()):
        if not manager.set_extension_enabled_immediate(tensors, True):
            raise RuntimeError(f'{tensors} is installed but could not be enabled; no PhysX fallback')
while app.is_running():
    app.update()
app.close()
sys.exit(0)
