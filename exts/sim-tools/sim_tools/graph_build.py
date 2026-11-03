"""sim.graph.build: create a whole OmniGraph (Action Graph) from one declarative spec.

Every student run built the lab's graphs by hand in run_python with og.Controller and hit the same
traps: short node names work only inside the CREATE_NODES edit, Make Array's second input must be
created by hand, prim targets are USD relationships, and a token such as Scale To/From Stage Units'
"conversion" silently fails with its key ("toStage") instead of its value ("Convert to stage units").
This tool takes node names, values, connections and prim targets, checks every port against the
created nodes, fixes what it safely can (and says so), and rolls the graph back on any error.

``parse_spec`` and ``split_port`` are pure and unit-tested without Kit.
"""

from __future__ import annotations

import re

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
PORT = re.compile(r"^(inputs|outputs|state):[A-Za-z_][A-Za-z0-9_:]{0,127}$")
DYNAMIC_INPUT = re.compile(r"^inputs:input(\d{1,2})$")
EVALUATORS = ("execution", "push", "dirty_push")
MAX_NODES = 64
MAX_EDGES = 256


class SpecError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def split_port(ref, names, graph_path=""):
    """'node.inputs:x' (or '<graph>/node.inputs:x') -> ('node', 'inputs:x'); node must be declared."""
    if not isinstance(ref, str) or "." not in ref:
        raise SpecError("INVALID_VALUE", f"{ref!r}: use '<node>.<port>', e.g. 'tick.outputs:tick'.")
    if graph_path and ref.startswith(graph_path.rstrip("/") + "/"):
        ref = ref[len(graph_path.rstrip("/")) + 1:]
    node, _, port = ref.partition(".")
    if node not in names:
        raise SpecError("UNKNOWN_NODE", f"{ref!r} names node {node!r}, which is not in nodes "
                        f"({', '.join(sorted(names)) or 'none'}).")
    if not PORT.match(port):
        raise SpecError("INVALID_VALUE", f"{ref!r}: the port must look like inputs:name or outputs:name.")
    return node, port


def parse_spec(a):
    """Validate and normalize the tool arguments (no Kit needed)."""
    nodes = a.get("nodes")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= MAX_NODES:
        raise SpecError("INVALID_VALUE", f"nodes: 1..{MAX_NODES} [name, node_type] pairs, e.g. "
                        "[\"tick\", \"omni.graph.action.OnPlaybackTick\"].")
    names, pairs = set(), []
    for item in nodes:
        if (not isinstance(item, (list, tuple)) or len(item) != 2
                or not all(isinstance(x, str) for x in item)):
            raise SpecError("INVALID_VALUE", f"nodes entry {item!r}: use [name, node_type].")
        name, kind = item
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
    values = a.get("values") or {}
    if not isinstance(values, dict) or len(values) > MAX_EDGES:
        raise SpecError("INVALID_VALUE", "values: an object of '<node>.inputs:<port>': value.")
    norm_values = []
    for ref, value in values.items():
        node, port = split_port(ref, names, graph_path)
        if not port.startswith("inputs:"):
            raise SpecError("INVALID_VALUE", f"{ref!r}: only inputs can be set.")
        norm_values.append((node, port, value))
    connections = a.get("connections") or []
    if not isinstance(connections, list) or len(connections) > MAX_EDGES:
        raise SpecError("INVALID_VALUE", f"connections: up to {MAX_EDGES} [from, to] pairs.")
    edges = []
    for item in connections:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise SpecError("INVALID_VALUE", f"connection {item!r}: use [\"node.outputs:x\", \"node.inputs:y\"].")
        src, dst = (split_port(x, names, graph_path) for x in item)
        if not dst[1].startswith("inputs:"):
            raise SpecError("INVALID_VALUE", f"connection {item!r}: the second end must be an input.")
        edges.append((src, dst))
    targets = a.get("targets") or {}
    if not isinstance(targets, dict) or len(targets) > MAX_EDGES:
        raise SpecError("INVALID_VALUE", "targets: an object of '<node>.inputs:<port>': [prim paths].")
    norm_targets = []
    for ref, paths in targets.items():
        node, port = split_port(ref, names, graph_path)
        if isinstance(paths, str):
            paths = [paths]
        if (not isinstance(paths, list) or not 1 <= len(paths) <= 64
                or not all(isinstance(p, str) and p.startswith("/") for p in paths)):
            raise SpecError("INVALID_VALUE", f"targets[{ref!r}]: absolute prim path(s), e.g. \"/World/robot\".")
        norm_targets.append((node, port, paths))
    evaluator = a.get("evaluator", "execution")
    if evaluator not in EVALUATORS:
        raise SpecError("INVALID_VALUE", f"evaluator: one of {', '.join(EVALUATORS)}.")
    return dict(nodes=pairs, values=norm_values, connections=edges, targets=norm_targets, evaluator=evaluator)


def token_value(value, allowed_raw):
    """(value to store, note) for a token input with allowed tokens {key: display}; raises on a bad value."""
    if not allowed_raw or not isinstance(value, str):
        return value, None
    shown = list(allowed_raw.values())
    if value in shown:
        return value, None
    if value in allowed_raw:
        return allowed_raw[value], f"'{value}' is the token key; stored its value '{allowed_raw[value]}'"
    raise SpecError("INVALID_VALUE", f"{value!r} is not allowed; use one of: {', '.join(map(repr, shown))}.")


# ---------------------------------------------------------------- Kit side

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
            return _refuse(SpecError("UNKNOWN_NODE_TYPE", f"No node type {kind!r} is registered. Look it up with "
                                     f"sim__graph__node_types contains={short!r}."))
    existing = stage.GetPrimAtPath(path)
    replaced = False
    if existing:
        if not a.get("replace"):
            return _refuse(SpecError("ALREADY_EXISTS", f"{path} exists. Pass replace=true to rebuild it (the old "
                                     "graph is deleted first), or choose another path."))
        if existing.GetTypeName() != "OmniGraph":
            return _refuse(SpecError("NOT_A_GRAPH", f"{path} is a {existing.GetTypeName() or 'typeless'} prim, "
                                     "not a graph; refusing to replace it."))
        stage.RemovePrim(path)
        replaced = True
    if not stage.GetPrimAtPath(Sdf.Path(path).GetParentPath()):
        return _refuse(SpecError("NOT_FOUND", f"The parent of {path} does not exist."))

    notes, created_ports = [], []
    keys = og.Controller.Keys
    try:
        og.Controller.edit({"graph_path": path, "evaluator_name": spec["evaluator"]},
                           {keys.CREATE_NODES: spec["nodes"]})

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
                raw = a_.get_metadata(og.MetadataKeys.ALLOWED_TOKENS_RAW)
            except Exception:  # noqa: BLE001
                pass
            value, note = token_value(value, json.loads(raw) if raw else None)
            if note:
                notes.append(f"{node}.{port}: {note}")
            try:
                set_value(node, port, value)
            except Exception:  # noqa: BLE001 - extended ("any") inputs resolve their type once connected
                deferred.append((node, port, value))
        for (s_node, s_port), (d_node, d_port) in spec["connections"]:
            og.Controller.connect(attr(s_node, s_port, False), attr(d_node, d_port, True))
        for node, port, value in deferred:
            try:
                attr(node, port, False)
                set_value(node, port, value)
            except Exception as exc:  # noqa: BLE001
                raise SpecError("INVALID_VALUE", f"Cannot set {node}.{port} = {value!r}: {str(exc)[:200]}") from exc
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
        stage.RemovePrim(path)
        exc.details["rolled_back"] = True
        exc.details["replaced_graph_lost"] = replaced
        return _refuse(exc)
    except Exception as exc:  # noqa: BLE001 - OmniGraph errors: roll back, report the message
        stage.RemovePrim(path)
        return _refuse(SpecError("BUILD_FAILED", f"{type(exc).__name__}: {str(exc)[:400]}", rolled_back=True,
                                 replaced_graph_lost=replaced))
    unsaved = [f"{node}.{port}" for node, port, _ in spec["values"]
               if stage.GetPrimAtPath(f"{path}/{node}").GetAttribute(port).Get() is None]
    if unsaved:
        notes.append("not authored in USD (runtime only, would not survive a save): " + ", ".join(unsaved))
    result = inspect_graph(stage, path, 128)
    result.update(built=True, replaced=replaced, created_ports=created_ports, notes=notes,
                  next="Play the timeline, then sim__graph__inspect again: nodes that failed to compute list "
                       "their errors there.")
    return result


def _refuse(exc):
    from isaac_mcp.project_assets import refuse

    refuse(exc.code, str(exc), **exc.details)
