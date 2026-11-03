"""OmniGraph discovery, graph inspection, the Action Graph editor, and Nav2 occupancy maps.

Added after a fresh agent did a full ROS 2 lab with these tools and had to dig through Isaac Sim's
install to learn node type ids and port names ("Make Array" is ``omni.graph.nodes.ConstructArray``,
Break 3-Vector takes ``inputs:tuple``), drive private editor APIs to show a graph in a video, and
reverse-engineer the occupancy-map buffer layout to write a map for Nav2.

``pgm_and_origin`` is pure (numpy only) so the map layout can be unit-tested without Kit.
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

PORT_TEXT = 160
MAX_DETAILED = 8


def _enable(extension):
    """Enable a Kit extension on demand (it is installed with Isaac Sim but not loaded by run.sh)."""
    import omni.kit.app

    em = omni.kit.app.get_app().get_extension_manager()
    if not em.is_extension_enabled(extension):
        em.set_extension_enabled_immediate(extension, True)
    return em.is_extension_enabled(extension)


def _short(value, limit=8):
    """A JSON-friendly, size-bounded copy of an attribute value."""
    try:
        import numpy as np

        if isinstance(value, np.ndarray):
            value = value.tolist()
    except ImportError:
        pass
    if isinstance(value, (list, tuple)):
        items = [_short(v, limit) for v in list(value)[:limit]]
        return items + ([f"... {len(value)} items"] if len(value) > limit else [])
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (int, bool, str)) or value is None:
        return value if not isinstance(value, str) else value[:200]
    return str(value)[:200]


def _text(value):
    text = " ".join(value) if isinstance(value, list) else str(value or "")
    return re.sub(r"\s+", " ", text).strip()[:PORT_TEXT]


def ogn_ports(ogn: dict, type_name: str) -> dict:
    """Inputs and outputs of *type_name* from a parsed .ogn file, with og-style names."""
    node = ogn.get(type_name) or next(iter(ogn.values()), {})
    out = {}
    for section in ("inputs", "outputs"):
        ports = {}
        for name, spec in (node.get(section) or {}).items():
            entry = {"type": spec.get("type")}
            if "default" in spec:
                entry["default"] = _short(spec["default"])
            if spec.get("optional"):
                entry["optional"] = True
            desc = _text(spec.get("description"))
            if desc:
                entry["description"] = desc
            ports[f"{section}:{name}"] = entry
        out[section] = ports
    return out


_RST_NAME = re.compile(r"\(\*(?P<name>(?:inputs|outputs):[^*]+)\*\)\s*$")


def rst_ports(text: str) -> dict:
    """Ports from a generated OmniGraph node .rst page (C++ nodes ship no .ogn file)."""
    import csv

    out = {"inputs": {}, "outputs": {}}
    for line in text.splitlines():
        if "(*inputs:" not in line and "(*outputs:" not in line:
            continue
        try:  # csv-table rows: quotes inside cells are doubled ("")
            cells = next(csv.reader([line.strip()], skipinitialspace=True))
        except (csv.Error, StopIteration):
            continue
        if len(cells) < 4:
            continue
        m = _RST_NAME.search(cells[0])
        if not m or not (cells[1].startswith("``") and cells[1].endswith("``")):
            continue
        m = {"name": m["name"], "type": cells[1][2:-2], "desc": cells[2], "default": cells[3]}
        name, kind = m["name"], m["type"]
        if kind.startswith("["):  # extended type: any of a long list
            n = kind.count("'") // 2
            kind = f"any of {n} types (resolved when connected)" if n > 6 else kind.replace("'", "")
        entry = {"type": kind}
        if m["default"] not in ("", "None"):
            entry["default"] = m["default"][:120]
        if m["desc"]:
            entry["description"] = _text(m["desc"])
        out["inputs" if name.startswith("inputs:") else "outputs"][name] = entry
    return out


_OGN_INDEX = {}


def _find_ogn(ext_path: str, type_name: str):
    """Ports of *type_name* from its .ogn file (or, for compiled nodes, its generated .rst page)."""
    if not ext_path:
        return None
    short = type_name.rsplit(".", 1)[-1]
    index = _OGN_INDEX.get(ext_path)
    if index is None:
        index = {}
        for pattern in ("*.ogn", "*.rst"):
            for path in Path(ext_path).rglob(pattern):
                index.setdefault(path.name, path)  # OgnROS2PublishClock.ogn -> path
        _OGN_INDEX[ext_path] = index
    # Most ids drop the Ogn prefix (omni.graph.nodes.ConstructArray -> OgnConstructArray.ogn); Isaac's
    # often keep it (isaacsim.core.nodes.OgnIsaacScaleToFromStageUnit -> OgnIsaacScaleToFromStageUnit.ogn).
    stems = [f"Ogn{short}"] + ([short] if short.startswith("Ogn") else [])
    try:
        for stem in stems:
            path = index.get(f"{stem}.ogn")
            if path is not None:
                text = path.read_text()
                return ogn_ports(json.loads(text), type_name), text
        for stem in stems:
            path = index.get(f"{stem}.rst")
            if path is not None:
                text = path.read_text()
                ports = rst_ports(text)
                if ports["inputs"] or ports["outputs"]:
                    return ports, text
    except (OSError, ValueError):
        return None
    return None


def node_types(contains, limit=20, ports=None):
    """Registered OmniGraph node types whose id or UI name contains *contains*."""
    import omni.graph.core as og
    import omni.kit.app

    needle = contains.strip().lower()
    em = omni.kit.app.get_app().get_extension_manager()
    found = []
    for name in sorted(og.get_registered_nodes()):
        nt = og.get_node_type(name)
        if not nt or not (nt.is_valid() if callable(getattr(nt, "is_valid", None)) else True):
            continue
        md = nt.get_all_metadata()
        ui = md.get("uiName", "")
        if needle in name.lower() or needle in ui.lower():
            found.append((name, nt, md))
    detailed = ports if ports is not None else len(found) <= 5
    result = []
    for i, (name, nt, md) in enumerate(found[:limit]):
        entry = {"type": name, "ui_name": md.get("uiName", ""), "extension": md.get("__extension", ""),
                 "description": _text(md.get("__description", ""))}
        dep = getattr(nt, "is_deprecated", False)
        if callable(dep):  # a method in some Kit versions, a property in others
            dep = dep()
        if dep:
            msg = getattr(nt, "deprecation_message", "")
            entry["deprecated"] = _text(msg() if callable(msg) else msg) or True
        if detailed and i < MAX_DETAILED:
            ext = md.get("__extension")
            ext_id = em.get_enabled_extension_id(ext) if ext else None
            found_ports = _find_ogn(em.get_extension_path(ext_id), name) if ext_id else None
            if found_ports:
                ports, source = found_ports
                entry.update(ports)
                if "arraySize" in source:
                    entry["dynamic_inputs_note"] = (
                        "Only inputs:input0 exists at first. Setting inputs:arraySize from a script does not add "
                        "inputs:input1..; add each with og.Controller.create_attribute(node, 'inputs:input1', "
                        "'any', og.AttributePortType.ATTRIBUTE_PORT_TYPE_INPUT), then connect it.")
            else:
                entry["ports"] = "not available (no .ogn file found); build one and use sim__graph__inspect"
        result.append(entry)
    return dict(
        matches=len(found),
        returned=len(result),
        node_types=result,
        usage=("Create nodes with og.Controller.edit(graph, {keys.CREATE_NODES: [(name, type)]}); connect with "
               "full paths, e.g. '<graph>/<node>.outputs:tick' -> '<graph>/<other>.inputs:execIn'."),
    )


def _graph_path(path):
    from isaac_mcp.project_assets import refuse

    from pxr import Sdf

    valid = Sdf.Path.IsValidPathString(path) if isinstance(path, str) else False
    if isinstance(valid, tuple):  # some USD versions return (False, message) for invalid paths
        valid = valid[0]
    if not bool(valid):
        refuse("INVALID_VALUE", "path must be an absolute prim path like /World/ROS_Clock.")
    p = Sdf.Path(path)
    if not (p.IsAbsolutePath() and p.IsPrimPath()):
        refuse("INVALID_VALUE", "path must be an absolute prim path like /World/ROS_Clock.")
    return path


def _headless():
    import carb.settings
    import sys

    settings = carb.settings.get_settings()
    return (settings.get("/app/window/hideUi") or settings.get("/app/window/enabled") is False
            or os.environ.get("UR5E_DT_HEADLESS") == "1" or "--no-window" in sys.argv)


def inspect_graph(stage, path, limit=64):
    """Nodes of an OmniGraph with their types, input values and connections."""
    import omni.graph.core as og
    from isaac_mcp.project_assets import refuse

    graph = og.get_graph_by_path(_graph_path(path))
    if graph is None or not graph.is_valid():
        prim = stage.GetPrimAtPath(path)
        refuse("NOT_A_GRAPH", f"No OmniGraph at {path}" + (f" (prim type {prim.GetTypeName()})" if prim else "")
               + ". Find graphs with sim__prim__find type_name=OmniGraph.")
    nodes = list(graph.get_nodes())
    out = []
    for node in nodes[:limit]:
        inputs, outputs = {}, {}
        for attr in node.get_attributes():
            name = attr.get_name()
            if name.startswith("inputs:"):
                entry = {"type": attr.get_resolved_type().get_ogn_type_name()}
                upstream = [c.get_path() for c in attr.get_upstream_connections()]
                if upstream:
                    entry["from"] = upstream
                else:
                    try:
                        entry["value"] = _short(og.Controller.get(attr))
                    except Exception as exc:  # unresolved extended types have no value yet
                        entry["value"] = f"<unreadable: {type(exc).__name__}>"
                inputs[name] = entry
            elif name.startswith("outputs:"):
                downstream = [c.get_path() for c in attr.get_downstream_connections()]
                outputs[name] = {"type": attr.get_resolved_type().get_ogn_type_name(),
                                 **({"to": downstream} if downstream else {})}
        out.append(dict(path=node.get_prim_path(), type=node.get_type_name(), inputs=inputs, outputs=outputs))
    return dict(graph=path, evaluator=graph.get_evaluator_name(), node_count=len(nodes),
                truncated=len(nodes) > limit, nodes=out)


def show_graph_open(stage, path, width=0, height=0):
    """Step 1 of sim.graph.show: open the editor on *path* (its window is built on a later frame)."""
    from isaac_mcp.project_assets import refuse

    if _headless():
        refuse("HEADLESS", "The Action Graph editor needs a windowed Isaac Sim (sim__launch headless=false).")
    import omni.graph.core as og

    graph = og.get_graph_by_path(_graph_path(path))
    if graph is None or not graph.is_valid():
        refuse("NOT_A_GRAPH", f"No OmniGraph at {path}.")
    _enable("omni.graph.window.action")
    try:
        import omni.graph.window.action.action_graph_extension as editor
        import omni.ui as ui

        editor.ActionGraphExtension.show_graph([stage.GetPrimAtPath(path)])
    except (ImportError, AttributeError) as exc:
        _editor_changed(exc)


def show_graph_finish(stage, path, layout=False, width=0, height=0):
    """Step 2 of sim.graph.show, a few frames later: size the window, load the graph, lay it out
    with expanded (readable) nodes, frame them."""
    try:
        import omni.graph.window.action.action_graph_extension as editor
        import omni.ui as ui
        from pxr import Sdf, Usd

        window = ui.Workspace.get_window("Action Graph")
        if window is not None and width and height:
            window.undock()
            window.width, window.height = float(width), float(height)
            window.position_x, window.position_y = 40.0, 60.0
        if layout:  # expanded nodes show their port names in a video
            for prim in Usd.PrimRange(stage.GetPrimAtPath(path)):
                if prim.HasAttribute("ui:nodegraph:node:pos") or prim.GetTypeName() == "OmniGraphNode":
                    prim.CreateAttribute("ui:nodegraph:node:expansionState", Sdf.ValueTypeNames.Token).Set("open")
        ext = editor._extension_instance  # noqa: SLF001 - the editor has no public API for this
        ext._window._main_widget._open_graph(path)  # noqa: SLF001
        view = ext._actions._get_view()  # noqa: SLF001
        if layout:
            view.layout_all()  # writes node positions (ui:nodegraph:node:pos) into the stage
        view.focus_on_nodes()
    except (ImportError, AttributeError) as exc:
        _editor_changed(exc)
    return dict(graph=path, shown=True, laid_out=bool(layout),
                note="Take a screenshot or record the window to capture it.")


def show_graph_refocus():
    """Frame all nodes again once expanded nodes have been drawn at their final size."""
    try:
        import omni.graph.window.action.action_graph_extension as editor

        editor._extension_instance._actions._get_view().focus_on_nodes()  # noqa: SLF001
    except (ImportError, AttributeError):
        pass


def _editor_changed(exc):
    from isaac_mcp.project_assets import refuse

    refuse("EDITOR_API_CHANGED", f"This Isaac Sim's Action Graph editor API differs ({type(exc).__name__}: "
           f"{str(exc)[:200]}); open the graph from Window > Graph Editors > Action Graph instead.")


def pgm_and_origin(buffer, dims_x, dims_y, min_bound, cell_size, meters_per_unit, clear=()):
    """Nav2 PGM bytes and YAML origin from an Isaac occupancy-map buffer.

    The buffer is row-major, ``dims_x`` cells per row, with 1.0 = occupied and 0.0 = free; Isaac's
    occupancy-map window shows it rotated 180 degrees for ROS (its default "180" rotation), which
    puts +x to the right and +y up as map_server expects. The origin is the world position of the
    lower-left corner of the lower-left cell, in metres. ``clear`` circles ``[x, y, radius]``
    (metres) are marked free, e.g. the robot's own footprint at its start pose.
    """
    import numpy as np

    grid = np.asarray(buffer, dtype=np.float32).reshape(dims_y, dims_x)
    grid = np.rot90(grid, 2).copy()
    origin = (float(min_bound[0]) * meters_per_unit, float(min_bound[1]) * meters_per_unit)
    res = cell_size * meters_per_unit
    if len(clear):
        rows, cols = np.indices(grid.shape)
        wx = origin[0] + (cols + 0.5) * res
        wy = origin[1] + (grid.shape[0] - 1 - rows + 0.5) * res
        for cx, cy, r in clear:
            grid[(wx - cx) ** 2 + (wy - cy) ** 2 <= r * r] = 0.0
    pgm = np.full(grid.shape, 205, dtype=np.uint8)  # unknown
    pgm[grid == 0.0] = 254                          # free
    pgm[grid == 1.0] = 0                            # occupied
    header = f"P5\n# isaac-mcp sim__map__occupancy\n{dims_x} {dims_y}\n255\n".encode()
    return header + pgm.tobytes(), origin, dict(occupied=int((grid == 1.0).sum()), free=int((grid == 0.0).sum()),
                                                unknown=int(grid.size - (grid == 1.0).sum() - (grid == 0.0).sum()))


def _numbers(v, n):
    """A list of *n* finite numbers (bools are not numbers here)."""
    return isinstance(v, list) and len(v) == n and all(type(x) in (int, float) and math.isfinite(x) for x in v)


def validate_occupancy(scene, stage, a):
    """Check every argument with no side effects, so refusals happen before any Play."""
    from isaac_mcp.project_assets import refuse
    from pxr import UsdGeom

    lo, hi, z = a.get("min"), a.get("max"), a.get("z", [0.05, 0.3])
    for v, n in ((lo, "min"), (hi, "max"), (z, "z")):
        if not _numbers(v, 2):
            refuse("INVALID_VALUE", f"{n} must be two finite numbers (metres).")
    if not (hi[0] > lo[0] and hi[1] > lo[1] and z[1] > z[0]):
        refuse("INVALID_VALUE", "max must exceed min, and z[1] must exceed z[0].")
    cell = a.get("cell_size", 0.05)
    if type(cell) not in (int, float) or not 0.005 <= cell <= 1.0:
        refuse("INVALID_VALUE", "cell_size must be 0.005..1.0 m.")
    start = a.get("start") or [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2]
    if not (_numbers(start, 2) and lo[0] < start[0] < hi[0] and lo[1] < start[1] < hi[1]):
        refuse("INVALID_VALUE", "start must be [x, y] metres inside the bounds, in free space.")
    if UsdGeom.GetStageUpAxis(stage) != UsdGeom.Tokens.z:
        refuse("UNSUPPORTED_STAGE", "Occupancy maps need a Z-up stage (ROS convention).")
    if (hi[0] - lo[0]) * (hi[1] - lo[1]) / (cell * cell) > 16_000_000:
        refuse("LIMIT", "Map larger than 16 million cells; increase cell_size or shrink the bounds.")
    base = a.get("basename", "map")
    if not isinstance(base, str) or not re.fullmatch(r"[A-Za-z0-9_\-]{1,60}", base):
        refuse("INVALID_OUTPUT", "basename: letters, digits, _ and - only (<=60), no extension.")
    clear = a.get("clear") or []
    if not (isinstance(clear, list) and len(clear) <= 16 and all(_numbers(c, 3) and c[2] > 0 for c in clear)):
        refuse("INVALID_VALUE", "clear: up to 16 [x, y, radius] circles in metres.")
    pgm_path, yaml_path = scene.output / f"{base}.pgm", scene.output / f"{base}.yaml"
    for p in (pgm_path, yaml_path):
        if p.exists():
            refuse("CONFLICT", f"{p} exists; choose another basename.")
    return dict(lo=lo, hi=hi, z=z, cell=float(cell), start=start, base=base, clear=clear,
                pgm_path=pgm_path, yaml_path=yaml_path)


def cell_value(pgm, width, height, origin, res, x, y):
    """PGM value (0 occupied, 254 free, 205 unknown) of the cell containing world (x, y)."""
    col = int(math.floor((x - origin[0]) / res))
    row = height - 1 - int(math.floor((y - origin[1]) / res))
    if not (0 <= col < width and 0 <= row < height):
        return None
    body = pgm[len(pgm) - width * height:]
    return body[row * width + col]


def cells_at(pgm, width, height, origin, res, x, y):
    """PGM values of the cells touching world (x, y): one cell, or up to four when the point lies
    on a cell edge (floating-point floor could pick either side)."""
    eps = res * 1e-6
    cols = {int(math.floor((x - origin[0] + d) / res)) for d in (-eps, eps)}
    rows = {height - 1 - int(math.floor((y - origin[1] + d) / res)) for d in (-eps, eps)}
    body = pgm[len(pgm) - width * height:]
    return [body[r * width + c] for r in rows for c in cols if 0 <= c < width and 0 <= r < height]


def start_blocked(counts, start_cells, start_in_collider=False):
    """Why the flood-fill start makes the map unusable, or None. Pure, so it is unit-tested."""
    total = max(1, sum(counts.values()))
    if start_in_collider:
        return "is inside a collider (PhysX overlap at the start point)"
    if counts["occupied"] == 0 and counts["unknown"] > 0.5 * total:
        # Seen live: with start inside a solid collider the generator marks that collider's
        # interior free, finds no walls, and leaves everything else unknown.
        return "seems to be inside an obstacle or a closed space: no walls were found and most of the map is unknown"
    if 254 not in start_cells:
        return "is occupied" if 0 in start_cells else "is not in mapped free space"
    return None


def _start_in_collider(stage, start, z_mid, cell, to_su):
    """PhysX overlap test at the start point (physics is playing during generation)."""
    try:
        from omni.physx import get_physx_scene_query_interface

        hits = []
        get_physx_scene_query_interface().overlap_sphere(
            cell * 0.25 * to_su, (start[0] * to_su, start[1] * to_su, z_mid * to_su),
            lambda hit: hits.append(hit.rigid_body or hit.collision) or False, False)
        return bool(hits)
    except Exception:  # no PhysX scene query available: fall back to the map-based checks
        return False


def occupancy_map(scene, stage, a):
    """Generate a 2D occupancy map from the stage's colliders and write a Nav2 PGM + YAML."""
    from isaac_mcp.project_assets import refuse
    from pxr import UsdGeom

    v = validate_occupancy(scene, stage, a)
    lo, hi, z, cell, start, base, clear = (v[k] for k in ("lo", "hi", "z", "cell", "start", "base", "clear"))
    pgm_path, yaml_path = v["pgm_path"], v["yaml_path"]
    mpu = UsdGeom.GetStageMetersPerUnit(stage) or 1.0
    to_su = 1.0 / mpu
    if not _enable("isaacsim.asset.gen.omap"):
        refuse("EXTENSION_UNAVAILABLE", "isaacsim.asset.gen.omap could not be enabled in this Isaac Sim.")
    from isaacsim.asset.gen.omap.bindings import _omap

    in_collider = _start_in_collider(stage, start, (z[0] + z[1]) / 2, cell, to_su)
    om = _omap.acquire_omap_interface()
    try:
        # The generator flood-fills free space from this origin ("must be in unoccupied space").
        cz = (z[0] + z[1]) / 2 * to_su
        cx, cy = start[0] * to_su, start[1] * to_su
        om.set_cell_size(cell * to_su)
        om.set_transform((cx, cy, cz), (lo[0] * to_su - cx, lo[1] * to_su - cy, z[0] * to_su - cz),
                         (hi[0] * to_su - cx, hi[1] * to_su - cy, z[1] * to_su - cz))
        om.update()
        om.generate()
        dims = om.get_dimensions()
        buffer = om.get_buffer()
        min_bound = om.get_min_bound()
    finally:
        _omap.release_omap_interface(om)
    if not len(buffer) or dims.x * dims.y != len(buffer):
        refuse("MAP_EMPTY", "The occupancy map came back empty. It needs colliders (UsdPhysics.CollisionAPI) "
               "inside the bounds and a UsdPhysics.Scene on the stage.")
    pgm, origin, counts = pgm_and_origin(buffer, dims.x, dims.y, min_bound, cell * to_su, mpu, clear)
    reason = start_blocked(counts, cells_at(pgm, dims.x, dims.y, origin, cell, start[0], start[1]), in_collider)
    if reason:
        refuse("START_BLOCKED", f"start {start} {reason}. The generator flood-fills free space from it, so the "
               "map would be empty or wrong. Move start to open floor next to (not on) the robot; `clear` only "
               "edits the saved image. No files were written.", cells=counts)
    scene.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    pgm_path.write_bytes(pgm)
    yaml_text = (f"image: {base}.pgm\nmode: trinary\nresolution: {cell:g}\n"
                 f"origin: [{origin[0]:.4f}, {origin[1]:.4f}, 0.0]\nnegate: 0\n"
                 "occupied_thresh: 0.65\nfree_thresh: 0.25\n")
    yaml_path.write_text(yaml_text)
    total = max(1, sum(counts.values()))
    if counts["unknown"] > 0.5 * total:
        warn = (f"{counts['unknown'] * 100 // total}% of the map is unknown (not reachable from start "
                f"{start}): move start into the main free area, or the bounds into one room.")
    elif counts["occupied"] == 0:
        warn = "No occupied cells reached from start: nothing with a collider intersects the z slice there."
    else:
        warn = None
    return dict(pgm=str(pgm_path), yaml=str(yaml_path), width=dims.x, height=dims.y, resolution=cell,
                origin=[round(origin[0], 4), round(origin[1], 4), 0.0], cells=counts, yaml_text=yaml_text,
                **({"warning": warn} if warn else {}),
                usage="ros2 launch nav2_bringup localization_launch.py map:=<yaml> use_sim_time:=true")
