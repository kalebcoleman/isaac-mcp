# Isaac Sim 6.2 notes for 6.0 tutorials

Course labs and NVIDIA's ROS 2 tutorials often target Isaac Sim **6.0.1** (for example the RAS545
TurtleBot3 lab with ROS 2 Action Graphs, lidar, camera, TF, RViz 2 and Nav2). This repository runs
**6.2.0**, where a few nodes and APIs behave differently. Each note below was checked live on
Isaac Sim 6.2.0 (Ubuntu 22.04, bundled Humble bridge, RoboStack Humble client, 2026-11-03) with
`run_python` and `ros2 topic echo`. The 6.0 behaviour is what the tutorials describe; it was not
re-run here.

Before building a graph, look the node up with `sim__graph__node_types` (by type id or UI name): it
returns this build's exact type id, ports and allowed token values. `sim__graph__build` creates a
whole graph in one call and handles notes (b) and (c) for you: it stores a token's value when you
give its key, and it creates Make Array's extra `inputs:inputN` ports. See
[TOOLS-SCENE.md](TOOLS-SCENE.md).

## (a) Constant Token has no output port

`omni.graph.nodes.ConstantToken` has a single attribute, `inputs:value`. There is no
`outputs:value`; connecting from it fails with `OmniGraphError`. Connect **from `inputs:value`**:

```python
og.Controller.connect("/Graph/frame_name.inputs:value", "/Graph/make_array.inputs:input1")
```

## (b) Scale To/From Stage Units takes the token's value, not its key

`isaacsim.core.nodes.OgnIsaacScaleToFromStageUnit` has `inputs:conversion` with allowed tokens
`Convert to stage units` and `Convert to meters` (raw map
`{"toStage": "Convert to stage units", "toMeters": "Convert to meters"}`; the default is
`Convert to stage units`). Set the **value** string. A key such as `toStage` is not one of the allowed
values; such a wrong token makes the node output zeros without an error (found while building
`sim__graph__build`; `sim__graph__inspect` lists the node's compute errors). `sim__graph__build` stores the value when given the key.

## (c) Make Array needs its second input created

`omni.graph.nodes.ConstructArray` ("Make Array") starts with `inputs:arraySize`, `inputs:arrayType`,
`inputs:input0` and `outputs:array`. `inputs:input1` (and later ones) does not exist until you
create it; connecting to it before that fails:

```python
og.Controller.create_attribute(node, "inputs:input1", og.Type(og.BaseDataType.TOKEN),
                               og.AttributePortType.ATTRIBUTE_PORT_TYPE_INPUT)
```

`sim__graph__build` creates these inputs itself.

## (d) The 2D RTX lidar

Create the lab's 2D rotary lidar with the experimental RTX sensor API:

```python
from isaacsim.sensors.experimental.rtx import Lidar
lidar = Lidar.create("/turtlebot3_burger/base_scan/lidar", config="Example_Rotary_2D")
```

It authors an `OmniLidar` prim (applied schemas include `OmniSensorGenericLidarCoreAPI`). When the
config asset nests the sensor under a reference root, the transform goes on that root so the
sensor keeps the vendor mounting offset. Lidar acquisition needs Play. Motion BVH (`--motion-bvh`) is optional: without it scans are
correct, but distortion from motion within one scan is not modeled.

## (e) ROS 2 Publish Transform Tree: `IsaacComputeTransformTree` is preferred, not required

In 6.2, `isaacsim.ros2.bridge.ROS2PublishTransformTree` has two input paths:

1. **Prims only (6.0 tutorial style):** set `inputs:targetPrims` (and optionally
   `inputs:parentPrim`) on the publisher. This still works in 6.2: with the TurtleBot's
   articulation root `/turtlebot3_burger/base_footprint` as target and `/World` as parent, `/tf`
   carried the whole tree (`World -> base_footprint -> base_link -> base_scan, caster_back_link,
   wheel_left_link, wheel_right_link`), about 93 messages per second. Kit logs once:
   `OgnROS2PublishTransformTree: using targetPrims for internal computation is deprecated. Connect
   OgnIsaacComputeTransformTree to inputs:parentFrames/childFrames/translations/orientations instead.`
2. **With `isaacsim.core.nodes.IsaacComputeTransformTree` upstream (the 6.2 path):** tick ->
   compute `execIn`, compute `execOut` -> publisher `execIn`, and connect `outputs:parentFrames`,
   `childFrames`, `translations` and `orientations` to the publisher inputs of the same names.
   Set `targetPrims` (and `parentPrim`) on the **compute** node. The publisher uses these arrays when
   `parentFrames` is non-empty at its first compute.

So both student reports were right: TF works without the compute node, with a deprecation warning,
and the compute node is the supported 6.2 layout. One difference was measured on this scene:

| Compute node `targetPrims` | Frames on `/tf` |
|---|---|
| `/turtlebot3_burger/base_footprint` (the articulation root link) | only `World -> base_footprint` (repeatable after Stop/Play) |
| `/turtlebot3_burger` (the robot prim above it) | the full link tree, as with path 1 |

With the compute node, target the robot's top prim, and check the frames with `ros2 topic echo
/tf` or `scripts/ros2_tf_doctor.py`. With `parentPrim=/World` the compute path also logged
`[PoseTree] parent getObjectType eInvalid for '/World'` each frame while still publishing `World`
as the parent; leaving `parentPrim` empty (World) avoided the warning.

`sim__ros2__tf_setup` builds its own TF graphs and reports the frames; prefer it over hand-built
graphs unless the lab asks for its own graph.

## Other things labs run into

- The `PoseTree` warning naming a camera can repeat every frame. It is harmless, but it floods the log.
- A flattened checkpoint is large (about 97 MB for the RAS545 scene), because saves flatten everything.
- A LiDAR topic missing after reopening a saved scene has a known cause and fix; see [saved LiDAR scenes](TOOLS-SCENE.md#saved-lidar-scenes-and-scan).
- In the RAS545 starter, the ground plane is at z=-0.75, and it reports `composition_errors: 2` on opening. Neither was a fault in run 8.
- Lab instructions that assume ROS domain 0 need adapting if another simulator holds it. A saved graph can author its own domain: a `ROS2Context` node with `useDomainIDEnvVar` off publishes on its authored `domain_id`, whatever `ROS_DOMAIN_ID` and `sim__stage__info` say, so a shell on another domain sees no topics. Lab files were seen authoring different domains, so check the graph; `sim__graph__inspect` adds `ros_domain_note` when they differ.
- OpenCode Code Mode has no timers, so agents use shell sleeps, and tool results arrive as JSON inside a `result` string.

## ROS 2 distro

Isaac Sim 6.2 bundles both Humble and Jazzy bridge libraries. `run.sh` picks Humble on Ubuntu
22.04 and Jazzy otherwise, unless `ROS_DISTRO` is exported. Use the same distro for RViz 2, Nav2
and the `ros2` CLI: with `config/ros_dds.env` (Cyclone, loopback only), Jazzy -> Humble delivered 0 of 8
messages in a test on an Ubuntu 24.04 desktop (reported by the user; not re-run on the 22.04 PC). See
[ROS2-RVIZ.md](ROS2-RVIZ.md#installing-ros-2-for-rviz-2-and-the-doctor).
