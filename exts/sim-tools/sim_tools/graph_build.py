"""sim.graph.build: create a whole OmniGraph (Action Graph) from one declarative spec.

Building graphs by hand in run_python with og.Controller hits the same
traps every time: short node names work only inside the CREATE_NODES edit, Make Array's second input must be
created by hand, prim targets are USD relationships, and a token such as Scale To/From Stage Units'
"conversion" silently fails with its key ("toStage") instead of its value ("Convert to stage units").
This tool takes node names, values, connections and prim targets, checks every port against the
created nodes, fixes what it safely can (and says so), and rolls the graph back on any error.

``parse_spec`` and ``split_port`` are pure and unit-tested without Kit.
"""

from __future__ import annotations

import json
import re

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
PORT = re.compile(r"^(inputs|outputs|state):[A-Za-z_][A-Za-z0-9_:]{0,127}$")
DYNAMIC_INPUT = re.compile(r"^inputs:input(\d{1,2})$")
EVALUATORS = ("execution", "push", "dirty_push")
MAX_NODES = 64
MAX_EDGES = 256
MAX_JSON = 1024 * 1024
MAX_JSON_DEPTH = 64


class SpecError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def _json_container(value, field, shape):
    """Decode only structural JSON, never ordinary scalar input values."""
    if isinstance(value, str):
        if len(value) > MAX_JSON:
            raise SpecError("INVALID_VALUE", f"{field}: JSON exceeds {MAX_JSON} characters.")
        def reject_constant(constant):
            raise ValueError(f"invalid JSON constant: {constant}")

        try:
            value = json.loads(value, parse_constant=reject_constant)
        except (ValueError, RecursionError) as exc:
            raise SpecError("INVALID_VALUE", f"{field}: expected {shape}, or its JSON-encoded string.") from exc
    # Bound nesting for both decoded and native containers before validation or
    # formatting an error. JSON parsers and Python repr have recursion limits.
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, (list, dict)):
            if depth >= MAX_JSON_DEPTH:
                raise SpecError("INVALID_VALUE", f"{field}: expected {shape}; nesting is limited to {MAX_JSON_DEPTH} levels.")
            pending.extend((child, depth + 1) for child in (item.values() if isinstance(item, dict) else item))
    return value


def _sequence(value, field):
    shape = ('a list of [name, node_type] pairs or {"name", "type"} objects' if field == "nodes"
             else 'a list of [from, to] pairs or {"from", "to"} objects')
    value = _json_container(value, field, shape)
    if not isinstance(value, list):
        raise SpecError("INVALID_VALUE", f"{field}: expected {shape}, or its JSON-encoded string.")
    limit = MAX_NODES if field == "nodes" else MAX_EDGES
    if len(value) > 2 * limit:
        raise SpecError("INVALID_VALUE", f"{field}: at most {limit} pairs are allowed.")
    # Some clients flatten pairs into alternating strings. A JSON-encoded pair
    # starts with '[' or '{'; ordinary names/ports remain literal strings.
    if value and all(isinstance(x, str) and not x.lstrip().startswith(("[", "{")) for x in value):
        if len(value) % 2:
            raise SpecError("INVALID_VALUE", f"{field}: a flat alternating list must contain an even number of strings; expected {shape}.")
        value = [value[i:i + 2] for i in range(0, len(value), 2)]
    if len(value) > limit:
        raise SpecError("INVALID_VALUE", f"{field}: at most {limit} pairs are allowed.")
    return [_json_container(x, f"{field}[{i}]", shape) if isinstance(x, str) else x
            for i, x in enumerate(value)]


def split_port(ref, names, graph_path=""):
    """'node.inputs:x' (or '<graph>/node.inputs:x') -> ('node', 'inputs:x'); node must be declared."""
    if not isinstance(ref, str) or "." not in ref:
        raise SpecError("INVALID_VALUE", f"{ref!r}: use '<node>.<port>', e.g. 'tick.outputs:tick'.")
    if graph_path and ref.startswith(graph_path.rstrip("/") + "/"):
        ref = ref[len(graph_path.rstrip("/")) + 1:]
    node, _, port = ref.partition(".")
    if NAME.match(port) and ":" not in port:
        port = "?" + port  # bare port name: build() picks outputs:/inputs:/state: from the real node
    if node not in names:
        raise SpecError("UNKNOWN_NODE", f"{ref!r} names node {node!r}, which is not in nodes "
                        f"({', '.join(sorted(names)) or 'none'}).")
    if not port.startswith("?") and not PORT.match(port):
        raise SpecError("INVALID_VALUE", f"{ref!r}: the port must look like inputs:name or outputs:name.")
    return node, port


def _node_pair(item, notes):
    """(name, type) from [name, type], [type, name] (swapped back, with a note) or {"name","type"}."""
    if isinstance(item, dict):
        name = item.get("name") or item.get("id") or item.get("node")
        kind = item.get("type") or item.get("node_type") or item.get("type_id")
        if isinstance(name, str) and isinstance(kind, str):
            return name, kind
    elif isinstance(item, (list, tuple)) and len(item) == 2 and all(isinstance(x, str) for x in item):
        name, kind = item
        if "." in name and "." not in kind and NAME.match(kind):
            notes.append(f"nodes entry {list(item)!r} had the type first; read it as [name, type] = "
                         f"[{kind!r}, {name!r}]")
            return kind, name
        return name, kind
    raise SpecError("INVALID_VALUE", f"nodes entry {item!r}: use [name, node_type], e.g. "
                    "[\"tick\", \"omni.graph.action.OnPlaybackTick\"], or {\"name\": ..., \"type\": ...}.")


def _edge(item):
    """[from, to] from [a, b], [srcNode, srcPort, dstNode, dstPort] or {"from","to"} objects."""
    if isinstance(item, dict):
        src = item.get("from") or item.get("source") or item.get("src")
        dst = item.get("to") or item.get("target") or item.get("dst")
        if isinstance(src, str) and isinstance(dst, str):
            return src, dst
    elif isinstance(item, (list, tuple)) and all(isinstance(x, str) for x in item):
        if len(item) == 2:
            return item[0], item[1]
        if len(item) == 4:  # a bare port name is resolved against the created node in build()
            return f"{item[0]}.{item[1]}", f"{item[2]}.{item[3]}"
    raise SpecError("INVALID_VALUE", f"connection {item!r}: use [\"node.outputs:x\", \"node.inputs:y\"] "
                    "(or [srcNode, srcPort, dstNode, dstPort], or {\"from\": ..., \"to\": ...}).")


def parse_spec(a):
    """Validate and normalize the tool arguments (no Kit needed)."""
    notes = []
    nodes = _sequence(a.get("nodes"), "nodes")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= MAX_NODES:
        raise SpecError("INVALID_VALUE", f"nodes: 1..{MAX_NODES} [name, node_type] pairs, e.g. "
                        "[\"tick\", \"omni.graph.action.OnPlaybackTick\"].")
    names, pairs = set(), []
    for item in nodes:
        name, kind = _node_pair(item, notes)
        if not NAME.match(name):
            raise SpecError("INVALID_VALUE", f"node name {name!r}: letters, digits and _, starting with a letter.")
        if name in names:
            raise SpecError("INVALID_VALUE", f"node name {name!r} is used twice.")
        if not re.fullmatch(r"[A-Za-z0-9_.]{3,200}", kind) or "." not in kind:
            raise SpecError("INVALID_VALUE", f"node type {kind!r}: a full type id such as "
                            "isaacsim.ros2.bridge.ROS2PublishClock (find it with sim__graph__node_types).")
        names.add(name)
        pairs.append((name, kind))
    graph_path = a.get("path", "")
    values = _json_container(a.get("values") if a.get("values") is not None else {}, "values", "an object of '<node>.inputs:<port>': value")
    if not isinstance(values, dict) or len(values) > MAX_EDGES:
        raise SpecError("INVALID_VALUE", "values: an object of '<node>.inputs:<port>': value.")
    norm_values = []
    for ref, value in values.items():
        node, port = split_port(ref, names, graph_path)
        if port.startswith("?"):
            port = "inputs:" + port[1:]
        if not port.startswith("inputs:"):
            raise SpecError("INVALID_VALUE", f"{ref!r}: only inputs can be set.")
        norm_values.append((node, port, value))
    connections = _sequence(a.get("connections") if a.get("connections") is not None else [], "connections")
    if not isinstance(connections, list) or len(connections) > MAX_EDGES:
        raise SpecError("INVALID_VALUE", f"connections: up to {MAX_EDGES} [from, to] pairs.")
    edges = []
    for item in connections:
        src, dst = (split_port(x, names, graph_path) for x in _edge(item))
        if dst[1].startswith("?"):
            dst = (dst[0], "inputs:" + dst[1][1:])
        if not dst[1].startswith("inputs:"):
            raise SpecError("INVALID_VALUE", f"connection {item!r}: the second end must be an input.")
        edges.append((src, dst))
    targets = _json_container(a.get("targets") if a.get("targets") is not None else {}, "targets", "an object of '<node>.inputs:<port>': [prim paths]")
    if not isinstance(targets, dict) or len(targets) > MAX_EDGES:
        raise SpecError("INVALID_VALUE", "targets: an object of '<node>.inputs:<port>': [prim paths].")
    norm_targets = []
    for ref, paths in targets.items():
        node, port = split_port(ref, names, graph_path)
        if port.startswith("?"):
            port = "inputs:" + port[1:]
        if isinstance(paths, str):
            paths = (_json_container(paths, f"targets[{ref!r}]", "a prim path or list of prim paths")
                     if paths.lstrip().startswith("[") else [paths])
        if (not isinstance(paths, list) or not 1 <= len(paths) <= 64
                or not all(isinstance(p, str) and p.startswith("/") for p in paths)):
            raise SpecError("INVALID_VALUE", f"targets[{ref!r}]: absolute prim path(s), e.g. \"/World/robot\".")
        norm_targets.append((node, port, paths))
    evaluator = a.get("evaluator", "execution")
    if evaluator not in EVALUATORS:
        raise SpecError("INVALID_VALUE", f"evaluator: one of {', '.join(EVALUATORS)}.")
    return dict(nodes=pairs, values=norm_values, connections=edges, targets=norm_targets, evaluator=evaluator,
                notes=notes)


def token_map(raw):
    """Isaac's ALLOWED_TOKENS_RAW is a JSON dict {key: value}, a JSON list, or a comma string."""
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, list):
        return {str(t): str(t) for t in raw}
    if isinstance(raw, str):
        return {t.strip(): t.strip() for t in raw.split(",") if t.strip()}
    return {}


SCRIPT_PORTS = ("inputs:script", "inputs:scriptPath")


def scripts_disabled():
    """A node that runs Python from its inputs is code execution, like sim__controller__create: same consent."""
    import os

    return os.getenv("ISAAC_MCP_ENABLE_SCENE_SCRIPTS") != "1"


def type_shape(og_type):
    """An OmniGraph type as (base, tuple count, array depth, role), lower-case names."""
    name = lambda value: str(value).rsplit(".", 1)[-1].lower()
    return name(og_type.base_type), og_type.tuple_count, og_type.array_depth, name(og_type.role)


TEXT_TYPES = {"token", "string", "path"}
TEXT_ROLES = {"text", "path"}


def _as_text(shape):
    """OmniGraph models string as uchar[] with a text role, and connects it to token, so count it as one text value."""
    base, tuples, depth, role = shape
    return ("token", tuples, 0, role) if base == "uchar" and depth == 1 and role in TEXT_ROLES else shape


def type_mismatch(source, target):
    """Why these two types can't connect, or None. Only what OmniGraph cannot convert: numeric widths convert, so they
    pass, and an extended ("any") port has no base type until it is connected, so it passes too."""
    if "unknown" in (source[0], target[0]):
        return None
    source, target = _as_text(source), _as_text(target)
    if (source[3] == "execution") != (target[3] == "execution"):
        return "one is an execution port and the other carries data"
    if source[2] != target[2]:
        return "one is an array and the other is not"
    if source[1] != target[1]:
        return f"tuple sizes differ ({source[1]} and {target[1]})"
    if (source[0] in TEXT_TYPES) != (target[0] in TEXT_TYPES):
        return "one is text and the other is a number"
    return None


def wire_dropped(source, target):
    """Whether Kit left this connection out. Its connect call can't tell: on 6.0.0.0 it returns false even when the wire
    is made, and only logs a warning when it refuses. No way to read the wire on this build counts as made."""
    try:
        upstream = target.get_upstream_connections()
    except AttributeError:
        return False
    return not any(attr.get_path() == source.get_path() for attr in upstream)


def token_value(value, allowed_raw):
    """(value to store, note) for a token input with allowed tokens; raises on a bad value."""
    allowed_raw = token_map(allowed_raw)
    if not allowed_raw or not isinstance(value, str):
        return value, None
    shown = list(allowed_raw.values())
    if value in shown:
        return value, None
    if value in allowed_raw:
        return allowed_raw[value], f"'{value}' is the token key; stored its value '{allowed_raw[value]}'"
    raise SpecError("INVALID_VALUE", f"{value!r} is not allowed; use one of: {', '.join(map(repr, shown))}.")


# ---------------------------------------------------------------- Kit side

def unstamped_clocks(spec):
    """Clock publishers nothing gives a time to. They publish happily, and every stamp is 0."""
    stamped = {node for _, (node, port) in spec["connections"] if port == "inputs:timeStamp"}
    stamped |= {node for node, port, _ in spec["values"] if port == "inputs:timeStamp"}
    return [name for name, kind in spec["nodes"] if kind.rsplit(".", 1)[-1] == "ROS2PublishClock" and name not in stamped]


def build(stage, a, stopped):
    import json

    import omni.graph.core as og
    from pxr import Sdf

    from .graph import _graph_path, inspect_graph

    try:
        spec = parse_spec(a)
    except SpecError as exc:
        return _refuse(exc)
    path = a.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        return _refuse(SpecError("INVALID_VALUE", "path: the absolute graph prim path, e.g. /World/ROS_Clock."))
    path = _graph_path(path)
    if not stopped:
        return _refuse(SpecError("TIMELINE_ACTIVE", "Stop the timeline first; a graph built while playing starts "
                                 "computing half-wired."))
    for _, kind in spec["nodes"]:
        nt = og.get_node_type(kind)
        if not nt or not (nt.is_valid() if callable(getattr(nt, "is_valid", None)) else True):
            short = kind.rsplit(".", 1)[-1]
            import difflib

            near = difflib.get_close_matches(kind, og.get_registered_nodes(), n=3, cutoff=0.7)
            hint = f" Did you mean {' or '.join(near)}?" if near else ""
            return _refuse(SpecError("UNKNOWN_NODE_TYPE", f"No node type {kind!r} is registered.{hint} Look it up with "
                                     f"sim__graph__node_types contains={short!r}."))
    existing = stage.GetPrimAtPath(path)
    replaced, backup = False, None
    if existing:
        if not a.get("replace"):
            return _refuse(SpecError("ALREADY_EXISTS", f"{path} exists. Pass replace=true to rebuild it (the old "
                                     "graph is put back if the rebuild is refused), or choose another path."))
        if existing.GetTypeName() != "OmniGraph":
            return _refuse(SpecError("NOT_A_GRAPH", f"{path} is a {existing.GetTypeName() or 'typeless'} prim, "
                                     "not a graph; refusing to replace it."))
        target_layer = stage.GetEditTarget().GetLayer()
        if any(spec.layer != target_layer for spec in existing.GetPrimStack()):
            return _refuse(SpecError("NOT_REPLACEABLE", f"{path} comes (partly) from another layer, a reference or a "
                                     "payload, so it cannot be deleted from here. Deactivate it, or build at "
                                     "another path."))
        backup = Sdf.Layer.CreateAnonymous()  # so a refused rebuild puts the old graph back instead of losing it
        Sdf.CreatePrimInLayer(backup, path)
        Sdf.CopySpec(target_layer, path, backup, path)
        stage.RemovePrim(path)
        if stage.GetPrimAtPath(path):
            return _refuse(SpecError("NOT_REPLACEABLE", f"{path} is still defined after removing it from the edit "
                                     "layer; build at another path.", replaced_graph_lost=True))
        replaced = True
    if not stage.GetPrimAtPath(Sdf.Path(path).GetParentPath()):
        return _refuse(SpecError("NOT_FOUND", f"The parent of {path} does not exist."))

    notes, created_ports = list(spec["notes"]), []
    notes += [f"{name}.inputs:timeStamp is not connected, so every /clock stamp will be 0. Connect "
              "isaacsim.core.nodes.IsaacReadSimulationTime's outputs:simulationTime to it." for name in unstamped_clocks(spec)]
    keys = og.Controller.Keys
    try:
        og.Controller.edit({"graph_path": path, "evaluator_name": spec["evaluator"]},
                           {keys.CREATE_NODES: spec["nodes"]})

        for name, kind in spec["nodes"]:
            created = og.Controller.node(f"{path}/{name}")
            if scripts_disabled() and any(created.get_attribute_exists(p) for p in SCRIPT_PORTS):
                raise SpecError("SCRIPT_DISABLED", f"{name} ({kind}) runs Python, which is off unless the user starts Kit "
                                "with ISAAC_MCP_ENABLE_SCENE_SCRIPTS=1. Nothing was built.")

        def attr(node, port, create_dynamic):
            n = og.Controller.node(f"{path}/{node}")
            if n.get_attribute_exists(port):
                return n.get_attribute(port)
            m = DYNAMIC_INPUT.match(port)
            if create_dynamic and m and n.get_attribute_exists("inputs:input0"):
                og.Controller.create_attribute(n, port, "any", og.AttributePortType.ATTRIBUTE_PORT_TYPE_INPUT)
                created_ports.append(f"{node}.{port}")
                return n.get_attribute(port)
            valid = sorted(x.get_name() for x in n.get_attributes()
                           if x.get_name().startswith(("inputs:", "outputs:")))
            raise SpecError("UNKNOWN_PORT", f"{node} ({n.get_type_name()}) has no {port}. Its ports: {', '.join(valid)}.")

        def set_value(node, port, value):
            # Controller.edit SET_VALUES authors the value in USD; Controller.set only changes the runtime
            # (Fabric) copy, so a saved stage would have lost every value.
            og.Controller.edit(path, {keys.SET_VALUES: [(f"{path}/{node}.{port}", value)]})

        deferred = []
        for node, port, value in spec["values"]:
            a_ = attr(node, port, True)
            raw = None
            try:
                from .compat import metadata_keys

                raw = a_.get_metadata(metadata_keys().ALLOWED_TOKENS_RAW)
            except Exception:  # noqa: BLE001
                pass
            try:
                parsed = json.loads(raw) if raw else None
            except ValueError:
                parsed = raw  # a plain comma-separated string
            value, note = token_value(value, parsed)
            if note:
                notes.append(f"{node}.{port}: {note}")
            try:
                set_value(node, port, value)
            except Exception:  # noqa: BLE001 - extended ("any") inputs resolve their type once connected
                deferred.append((node, port, value))
        def source_port(node, port):
            # A bare source port: outputs: first, then a constant node's inputs:value, then state:.
            if not port.startswith("?"):
                return port
            n = og.Controller.node(f"{path}/{node}")
            for prefix in ("outputs:", "inputs:", "state:"):
                if n.get_attribute_exists(prefix + port[1:]):
                    note = f"{node}.{port[1:]}: no output of that name; connected from {prefix}{port[1:]}"
                    if prefix != "outputs:" and note not in notes:
                        notes.append(note)
                    return prefix + port[1:]
            return "outputs:" + port[1:]  # attr() then refuses with the node's real ports

        for (s_node, s_port), (d_node, d_port) in spec["connections"]:
            source, target = attr(s_node, source_port(s_node, s_port), False), attr(d_node, d_port, True)
            try:
                mismatch = type_mismatch(type_shape(source.get_resolved_type()), type_shape(target.get_resolved_type()))
            except AttributeError:  # no type information on this build: let Kit decide, as before
                mismatch = None
            if mismatch:
                raise SpecError("TYPE_MISMATCH", f"{s_node}.{s_port} cannot drive {d_node}.{d_port}: {mismatch} "
                                f"({source.get_resolved_type()} to {target.get_resolved_type()}). Nothing was built. "
                                "sim__graph__node_types lists each node's ports and types.")
            og.Controller.connect(source, target)
            if wire_dropped(source, target):
                raise SpecError("CONNECTION_REFUSED", f"Kit did not connect {s_node}.{s_port} to {d_node}.{d_port} "
                                f"({source.get_resolved_type()} to {target.get_resolved_type()}); it only logs a warning. "
                                "Nothing was built. For text, set the input's value in values instead of wiring it.")
        for node, port, value in deferred:
            try:
                attr(node, port, False)
                set_value(node, port, value)
            except Exception as exc:  # noqa: BLE001
                raise SpecError("INVALID_VALUE", f"Cannot set {node}.{port} (type {attr(node, port, False).get_resolved_type()}) "
                                f"= {value!r}: {str(exc)[:200]}") from exc
        for node, port, paths in spec["targets"]:
            prim = stage.GetPrimAtPath(f"{path}/{node}")
            rel = prim.GetRelationship(port)
            if not rel:
                raise SpecError("UNKNOWN_PORT", f"{node}.{port} is not a prim-target input (no relationship); set "
                                "plain values with values, not targets.")
            missing = [p for p in paths if not stage.GetPrimAtPath(p)]
            if missing:
                notes.append(f"{node}.{port}: target(s) not on the stage yet: {', '.join(missing)}")
            rel.SetTargets([Sdf.Path(p) for p in paths])
    except SpecError as exc:
        exc.details.update(rolled_back=True, replaced_graph_lost=_roll_back(stage, path, backup))
        return _refuse(exc)
    except Exception as exc:  # noqa: BLE001 - OmniGraph errors: roll back, report the message
        return _refuse(SpecError("BUILD_FAILED", f"{type(exc).__name__}: {str(exc)[:400]}", rolled_back=True,
                                 replaced_graph_lost=_roll_back(stage, path, backup)))
    unsaved = [f"{node}.{port}" for node, port, _ in spec["values"]
               if stage.GetPrimAtPath(f"{path}/{node}").GetAttribute(port).Get() is None]
    if unsaved:
        notes.append("not authored in USD (runtime only, would not survive a save): " + ", ".join(unsaved))
    if any(kind.startswith(("isaacsim.ros2.", "omni.isaac.ros2")) for _, kind in spec["nodes"]):
        from .ros_env import shell_hint

        notes.append(shell_hint())
    result = inspect_graph(stage, path, 128)
    result.update(built=True, replaced=replaced, created_ports=created_ports, notes=notes,
                  next="Play the timeline, then sim__graph__inspect again: nodes that failed to compute list "
                       "their errors there.")
    return result


def _roll_back(stage, path, backup):
    """Remove a failed build and put back the graph it was replacing. True if there was one and it could not be restored."""
    from pxr import Sdf

    stage.RemovePrim(path)
    if backup is None:
        return False
    layer = stage.GetEditTarget().GetLayer()
    Sdf.CreatePrimInLayer(layer, path)
    return not Sdf.CopySpec(backup, path, layer, path)


def _refuse(exc):
    from isaac_mcp.project_assets import refuse

    refuse(exc.code, str(exc), **exc.details)
