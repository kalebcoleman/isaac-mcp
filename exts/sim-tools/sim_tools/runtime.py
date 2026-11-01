"""Read actual Kit identity and check native adapter capabilities at use."""

import importlib.metadata
import inspect
import os
from pathlib import Path

from isaac_mcp.project_assets import refuse
from isaac_mcp.sim_versions import reconcile_identity, sensor_stack_issues


def runtime_identity():
    import isaacsim
    import omni.kit.app

    packages = {}
    for name in (
        "isaacsim",
        "isaacsim-kernel",
        "isaacsim-core",
        "isaacsim-sensor",
        "isaacsim-replicator",
    ):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    roots = {Path(isaacsim.__file__).parent}
    if os.getenv("ISAAC_PATH"):
        roots.add(Path(os.environ["ISAAC_PATH"]))
    try:
        import carb.tokens

        app_root = carb.tokens.get_tokens_interface().resolve("${app}")
        if app_root and "${" not in app_root:
            roots.add(Path(app_root))
    except (ImportError, AttributeError):
        pass
    builds = {}
    for root in roots:
        p = root / "VERSION"
        if p.is_file():
            builds[str(p)] = p.read_text()[:512].strip()
    try:
        from isaacsim.core.version import get_version

        core = get_version()[0]
    except (ImportError, IndexError, OSError):
        core = None
    app = omni.kit.app.get_app()
    manager = app.get_extension_manager()
    extensions = {}
    for name in (
        "omni.replicator.core",
        "omni.syntheticdata",
        "isaacsim.sensors.rtx",
        "isaacsim.sensors.experimental.rtx",
        "isaacsim.core.simulation_manager",
        "isaacsim.physics.newton",
        "isaacsim.physics.newton.tensors",
        "omni.warp.core",
    ):
        ext_id = manager.get_enabled_extension_id(name)
        if ext_id:
            entry = manager.get_extension_dict(ext_id)
            extensions[name] = {
                "id": ext_id,
                "version": entry.get("package", {}).get("version"),
                "enabled": True,
            }
        else:
            extensions[name] = {"enabled": False}
    result = reconcile_identity(
        packages, builds, core, app.get_build_version(), extensions
    )
    try:
        from isaacsim.core.simulation_manager import SimulationManager

        result["physics_engine"] = SimulationManager.get_active_physics_engine()
    except (ImportError, AttributeError):
        result["physics_engine"] = None
    result["physics_adapter"] = (
        "newton-direct-step-fabric-v1"
        if result["physics_engine"] == "newton"
        else "physx-simulation-manager-v1"
        if result["physics_engine"] == "physx"
        else None
    )
    if result["physics_engine"] == "newton":
        from isaacsim.physics.newton import acquire_stage

        native = acquire_stage()
        result["newton"] = {
            "initialized": bool(native and native.initialized),
            "solver": str(native.cfg.solver_cfg.solver_type) if native else None,
            "update_fabric": native.cfg.update_fabric if native else None,
            "use_cuda_graph": native.cfg.use_cuda_graph if native else None,
        }
    import sys

    result["python"] = sys.version
    result["physics_packages"] = {}
    for module_name, distribution in (
        ("newton", "newton"),
        ("mujoco", "mujoco"),
        ("mujoco_warp", "mujoco-warp"),
    ):
        module = sys.modules.get(module_name)
        try:
            version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            version = None
        result["physics_packages"][module_name] = {
            "installed_version": version,
            "loaded": module is not None,
            "module": getattr(module, "__file__", None),
            "loaded_version": getattr(module, "__version__", None),
        }
    try:
        import warp

        result["warp"] = {"version": warp.__version__, "module": warp.__file__}
    except ImportError:
        result["warp"] = None
    return result


def require_cpu_annotator(lidar=False):
    """Select the owned-CPU adapter; never fall back to a borrowed CUDA buffer.

    Validation covers these release builds, plus feature checks. Unknown builds
    must be qualified with the reproducible live suite before native capture.
    The CPU path does not depend on experimental CameraSensor being installed.
    """
    identity = runtime_identity()
    issues = sensor_stack_issues(identity, lidar=lidar)
    if issues:
        refuse(
            "SENSOR_BUILD_UNVERIFIED",
            "Cannot select a validated native sensor adapter: "
            + "; ".join(issues)
            + ". Read sim.stage.info for full identity.",
        )
    try:
        import omni.replicator.core as rep
        from omni.replicator.core.annotators import Annotator

        for func in (rep.AnnotatorRegistry.get_annotator, Annotator.get_data):
            if (
                not {"device", "do_array_copy"}
                <= inspect.signature(func).parameters.keys()
            ):
                refuse(
                    "SENSOR_API_UNAVAILABLE",
                    "Replicator lacks explicit owned CPU storage; no CUDA fallback.",
                )
        if not callable(getattr(Annotator, "detach", None)):
            refuse(
                "SENSOR_API_UNAVAILABLE",
                "Replicator lacks annotator lifecycle cleanup.",
            )
    except (ImportError, AttributeError, ValueError) as e:
        refuse(
            "SENSOR_API_UNAVAILABLE",
            "Enable the installed omni.replicator.core extension: " + str(e),
        )
    return rep, identity
