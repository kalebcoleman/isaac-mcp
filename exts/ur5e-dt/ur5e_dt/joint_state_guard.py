"""Refuse to build the twin's mirror graph when the simulation already publishes /joint_states.

The twin mirrors a real robot: its graph drives the arm from /joint_states. If this stage also publishes
the simulated state there (sim__ros2__tf_setup with its default topic), the arm chases its own delayed
state and oscillates at its velocity limit as soon as the timeline plays, with no error anywhere.
sim__ros2__tf_setup refuses this when the twin's graph already exists; this is the other order.
"""

MIRRORED_TOPIC = "/joint_states"


def resolved_topic(topic, namespace=""):
    topic = topic or ""
    if topic.startswith("/"):
        return topic
    ns = (namespace or "").strip("/")
    return f"/{ns}/{topic}" if ns else f"/{topic}"


def loop_publishers(nodes):
    """``nodes`` is (node path, node type, topicName, nodeNamespace). Which publish into the mirrored topic."""
    return [path for path, kind, topic, namespace in nodes
            if kind.rsplit(".", 1)[-1] == "ROS2PublishJointState" and resolved_topic(topic, namespace) == MIRRORED_TOPIC]


def refusal(publishers):
    return (f"{', '.join(publishers)} publishes this simulation's joint states on {MIRRORED_TOPIC}, which the twin's graph "
            "uses to mirror a real robot, so the arm would chase its own state and oscillate at its velocity limit once "
            "playing. Nothing was built. Stop the timeline and call sim__ros2__tf_setup again with "
            "joint_states_topic=isaac/joint_states (or delete that graph), then call this again.")


def stage_nodes():
    """Every node's identity on the current stage. Kit only."""
    import omni.graph.core as og

    found = []
    for graph in og.get_all_graphs():
        if getattr(graph, "is_disabled", lambda: False)():
            continue
        for node in graph.get_nodes():
            value = lambda name: (node.get_attribute(name).get() if node.get_attribute_exists(name) else "")
            found.append((node.get_prim_path(), node.get_type_name(), value("inputs:topicName"), value("inputs:nodeNamespace")))
    return found


def check_stage():
    """Raise when the current stage would make the loop. Kit only."""
    publishers = loop_publishers(stage_nodes())
    if publishers:
        raise RuntimeError(refusal(publishers))
