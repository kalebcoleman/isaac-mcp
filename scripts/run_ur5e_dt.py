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
# run.sh passes its choice in ISAAC_MCP_PHYSICS_BACKEND; run directly, the one configuration module
# decides (environment variable, then the project and user config files, then its built-in default).
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'mcp'))
from isaac_mcp import config
backend, _source = config.resolve_physics_backend()
if backend not in config.PHYSICS_BACKENDS:
    raise RuntimeError('physics_backend must be ' + ' or '.join(config.PHYSICS_BACKENDS))
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
else:
    # The mirror of the check above: a PhysX request must not end up on another engine. A build with
    # no engine query has only PhysX, so an answer that is not another known backend passes.
    try:
        from isaacsim.core.simulation_manager import SimulationManager
        active = str(SimulationManager.get_active_physics_engine()).lower()
    except (ImportError, AttributeError):
        active = backend
    if active in config.PHYSICS_BACKENDS and active != backend:
        raise RuntimeError(f'Requested {backend} engine is not active (the app runs {active}); no {active} fallback')
while app.is_running():
    app.update()
app.close()
sys.exit(0)
