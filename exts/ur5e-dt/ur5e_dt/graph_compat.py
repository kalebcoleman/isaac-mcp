"""Reuse fixed twin graphs without deleting opinions in another USD layer.

Quick Start may run with the session layer selected, or against a reopened
stage. RemovePrim only removes the selected layer's spec: a composed graph
can survive, and immediate recreation then fails in native OmniGraph. These
two fixed publisher graphs have no user-selected settings. Reuse a matching
contract; refuse a conflict rather than deleting it or resetting the stage.
"""


def _node_type(value):
    # NVIDIA moved the ROS node namespace while preserving legacy aliases.
    return str(value).replace("isaacsim.ros2.bridge.", "isaacsim.ros2.nodes.")


def ensure_fixed_graph(stage, og, config, specification):
    path = config["graph_path"]
    prim = stage.GetPrimAtPath(path)
    if not prim:
        return og.Controller.edit(config, specification)

    def require(condition, detail):
        if not condition:
            raise ValueError(
                f"Existing graph at {path} conflicts with the twin contract ({detail}); "
                "it was preserved. Inspect it or use an explicitly disposable new stage."
            )

    require(prim.GetTypeName() == "OmniGraph", "prim type")
    keys = og.Controller.Keys
    definitions = specification[keys.CREATE_NODES]
    for name, expected in definitions:
        node = stage.GetPrimAtPath(path + "/" + name)
        require(bool(node), "missing node " + name)
        require(
            _node_type(node.GetAttribute("node:type").Get()) == _node_type(expected),
            "node type " + name,
        )
    for name, expected in specification.get(keys.SET_VALUES, []):
        attribute = stage.GetAttributeAtPath(path + "/" + name)
        require(bool(attribute) and attribute.Get() == expected, "input " + name)
    for source, destination in specification.get(keys.CONNECT, []):
        attribute = stage.GetAttributeAtPath(path + "/" + destination)
        require(
            bool(attribute)
            and [str(p) for p in attribute.GetConnections()] == [path + "/" + source],
            "connection " + destination,
        )
    nodes = [og.Controller.node(path + "/" + name) for name, _ in definitions]
    require(all(node and node.is_valid() for node in nodes), "native node readiness")
    return og.get_graph_by_path(path), nodes, None, None
