"""Route ``assets__import_path`` to the right importer for the file's format, with one approval and one snapshot.

The host picks the format (``format="auto"`` reads the file name, or what a folder holds), makes the verified snapshot of the
approved folder (running the xacro or SDF converter over the copy when the file needs one) and tells the caller which Kit
importer to call with which options. Nothing here runs code from a file.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from . import asset_formats
from .project_assets import AssetError, refuse

_FORMATS_HERE = ("usd", "urdf", "xacro", "sdf", "mjcf", "mesh", "map")  # what this module routes; Kit always gets assets.import_path
_ROBOT_OPTIONS = ("fixed_base", "default_drives", "joint_targets")
_HELP = ("Supported: USD (.usd .usda .usdc .usdz), URDF (.urdf), xacro (.xacro), SDF (.sdf .world), MuJoCo MJCF (.xml), a mesh (.gltf .glb .obj .stl .fbx .dae .ply), a ROS map (.yaml), "
         "and a folder that holds one of them (a Gazebo model, a ROS package, a MuJoCo or LeRobot robot folder).")


def route(broker, params: dict) -> tuple[str, dict, dict]:
    """``(kit_tool, kit_params, info)`` for an ``assets.import_path`` call. *info* says what the host chose."""
    params = dict(params)
    fmt = params.pop("format", None) or "auto"
    format_was_auto = fmt == "auto"
    xacro_args = params.pop("xacro_args", None)
    sdf_model = params.pop("sdf_model", None)
    mesh_options = {k: params.pop(k) for k in ("scale", "up_axis", "as_collider", "mass") if k in params}
    wall_height = params.pop("wall_height", None)
    source, root_id, relative_path = params.pop("source_path", None), params.pop("root_id", None), params.pop("relative_path", None)
    if (source is None) == (root_id is None or relative_path is None):
        refuse("INVALID_VALUE", "Give source_path, or root_id with relative_path; not both and not neither.")
    if source is None:  # an approved root named by its ID (assets__roots), and a path inside it
        from .project_assets import relative

        known = broker.roots.get(root_id)
        if known is None:
            refuse("ROOT_NOT_APPROVED", f"{root_id!r} is not an approved root ID. assets__roots lists them.")
        source = str(Path(known[0]) / relative(relative_path))
    if fmt not in asset_formats.FORMATS:
        refuse("INVALID_VALUE", f"format must be one of {', '.join(asset_formats.FORMATS)}.")
    if xacro_args is not None and (not isinstance(xacro_args, dict) or len(xacro_args) > 64
                                   or not all(isinstance(k, str) and isinstance(v, (str, int, float, bool)) for k, v in xacro_args.items())):
        refuse("INVALID_VALUE", "xacro_args is an object of names to strings or numbers, for example {\"ur_type\": \"ur10e\"}.")
    if sdf_model is not None and (not isinstance(sdf_model, str) or len(sdf_model) > 128):
        refuse("INVALID_VALUE", "sdf_model is a model name.")
    info = {}
    folder = broker.locate_folder(source) if isinstance(source, str) else None
    chosen_from = None
    if folder is not None:
        root_id, rel_folder = folder
        files = broker.folder_files(root_id, rel_folder)
        found = asset_formats.candidates(files, lambda rel: broker.read_file(root_id, rel), rel_folder, Path(broker.roots[root_id][0]).name)
        picked = asset_formats.choose(found)
        chosen_from = dict(folder=source, entry=picked["entry"], format=picked["format"], why=picked["why"],
                           others=[dict(entry=c["entry"], format=c["format"]) for c in found if c["entry"] != picked["entry"]][:8])
        source = str(Path(broker.roots[root_id][0]) / picked["entry"])
        if fmt == "auto":
            fmt = picked["format"]
    if fmt == "auto":
        fmt = asset_formats.format_of(source)  # (format_was_auto stays true)
        if fmt is None and isinstance(source, str) and os.path.isdir(source):
            # A folder outside every approved root: say how to approve it, instead of that its name is not a format.
            from .project_assets import approval_help

            refuse("ROOT_NOT_APPROVED", "That folder is outside all user-approved project roots and the asset root." + approval_help(Path(source)))
        if fmt is None:
            refuse("UNSUPPORTED_FORMAT", f"{PurePosixPath(str(source)).name} is not a format the import knows. {_HELP}")
    info["format"] = fmt
    if chosen_from:
        info["bundle"] = chosen_from
    if fmt not in _FORMATS_HERE:
        refuse("UNSUPPORTED_FORMAT", f"format {fmt} is not available. {_HELP}")
    if fmt == "urdf" and format_was_auto:
        # Some packages (TurtleBot3) keep xacro in a file named .urdf: look at the file, not only its name.
        try:
            probe_root, probe_rel = broker.locate(source, kind="urdf")
            head = broker.read_file(probe_root, probe_rel)[:200_000]
        except (AssetError, OSError):
            head = b""
        if b"xacro:" in head or b"xmlns:xacro" in head:
            fmt = "xacro"
            info["format"] = "xacro"
    kind = {"usd": "usd", "urdf": "urdf", "xacro": "xacro", "sdf": "sdf", "mjcf": "mjcf", "mesh": "mesh", "map": "map"}[fmt]
    root_id, relative_path = broker.locate(source, kind=kind)
    known = broker.roots.get(root_id) or broker._pack_roots.get(root_id)
    root_name = Path(known[0]).name if known else ""
    generate = None
    if fmt == "xacro":
        generate = asset_formats.xacro_generator(xacro_args or {}, root_name)
    elif fmt == "sdf":
        generate = asset_formats.sdf_generator(sdf_model, root_name)
    elif xacro_args or sdf_model:
        refuse("INVALID_VALUE", f"xacro_args and sdf_model apply to xacro and SDF files, not {fmt}.")
    snapshot = broker.snapshot(root_id, relative_path, kind=kind, generate=generate)
    params = {**params, "snapshot_id": snapshot["snapshot_id"], "manifest_sha256": snapshot["manifest_sha256"]}
    if wall_height is not None and fmt != "map":
        refuse("INVALID_VALUE", f"wall_height applies to a map (a ROS map .yaml), not {fmt}.")
    if fmt == "map":
        if mesh_options:
            refuse("INVALID_VALUE", "scale, up_axis, as_collider and mass do not apply to a map.")
        for option in _ROBOT_OPTIONS:
            params.pop(option, None)
        params.update(format="map", **({"wall_height": wall_height} if wall_height is not None else {}))
    elif mesh_options and fmt != "mesh":
        refuse("INVALID_VALUE", f"scale, up_axis, as_collider and mass apply to a mesh file (glTF, OBJ, STL, ...), not {fmt}.")
    if fmt == "mesh":
        for option in _ROBOT_OPTIONS:
            params.pop(option, None)
        params.update(mesh_options, format="mesh")
    elif fmt == "usd":
        for option in _ROBOT_OPTIONS:
            params.pop(option, None)
    if fmt == "sdf":
        result = getattr(generate, "result", {})
        info.update(sdf_model=result.get("model"), sdf_models=result.get("models"), sdf_warnings=result.get("warnings", []),
                    sdf_dropped=result.get("dropped", {}))
        if params.get("fixed_base") is None:
            params["fixed_base"] = bool(result.get("static"))
    if fmt in ("xacro", "sdf"):
        info["converted_from"] = relative_path
    # Kit gets one tool and the format to run: xacro and SDF have become a URDF in the snapshot by now.
    params["format"] = {"xacro": "urdf", "sdf": "urdf"}.get(fmt, fmt)
    return "assets.import_path", params, info
