"""Blender template for ``hardware(action="render")``. Runs inside Blender; the server never imports it.

blender -b --factory-startup -P hardware_render.py -- --out OUT.png --size WxH --samples N INPUT...

Inputs are GLB or glTF (metres) and STL (millimetres, scaled to metres). One studio: floor, three area lights, a
camera framing everything from the front right. Cycles on OptiX when a device answers, otherwise the CPU. Writes only
OUT.png.
"""

import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def arguments():
    argv = sys.argv[sys.argv.index("--") + 1:]
    options = {"--out": None, "--size": "1600x1200", "--samples": "128"}
    files = []
    while argv:
        item = argv.pop(0)
        if item in options:
            options[item] = argv.pop(0)
        else:
            files.append(Path(item))
    width, height = (int(value) for value in options["--size"].split("x"))
    return Path(options["--out"]), width, height, int(options["--samples"]), files


def load(files):
    objects = []
    for path in files:
        before = set(bpy.data.objects)
        if path.suffix.lower() == ".stl":
            bpy.ops.wm.stl_import(filepath=str(path), global_scale=0.001)
        else:
            bpy.ops.import_scene.gltf(filepath=str(path))
        objects += [item for item in bpy.data.objects if item not in before]
    return [item for item in objects if item.type == "MESH"]


def bounds(objects):
    bpy.context.view_layer.update()
    points = [item.matrix_world @ Vector(corner) for item in objects for corner in item.bound_box]
    low = Vector([min(point[axis] for point in points) for axis in range(3)])
    high = Vector([max(point[axis] for point in points) for axis in range(3)])
    return low, high


def studio(scene, low, high):
    centre, radius = (low + high) / 2, max((high - low).length / 2, 1e-3)
    bpy.ops.mesh.primitive_plane_add(size=radius * 40, location=(centre.x, centre.y, low.z - radius * 0.002))
    target = bpy.data.objects.new("target", None)
    scene.collection.objects.link(target)
    target.location = centre

    def place(item, direction, distance):
        scene.collection.objects.link(item)
        item.location = centre + Vector(direction).normalized() * distance
        aim = item.constraints.new("TRACK_TO")
        aim.target, aim.track_axis, aim.up_axis = target, "TRACK_NEGATIVE_Z", "UP_Y"

    for name, direction, power in (("key", (1, -1.2, 1.6), 60), ("fill", (-1.6, -0.6, 0.8), 20), ("rim", (-0.4, 1.6, 1), 35)):
        light = bpy.data.lights.new(name, "AREA")
        light.size, light.energy = radius * 2, power * radius * radius * 100
        place(bpy.data.objects.new(name, light), direction, radius * 6)
    camera = bpy.data.cameras.new("camera")
    camera.lens = 70
    distance = radius * 1.2 / math.sin(camera.angle / 2)
    camera.clip_start, camera.clip_end = distance / 100, distance * 100
    scene.camera = bpy.data.objects.new("camera", camera)
    place(scene.camera, (0.95, -1.35, 1.15), distance)
    world = bpy.data.worlds.new("studio")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.15
    scene.world = world


def device(scene):
    scene.cycles.device = "CPU"
    try:
        preferences = bpy.context.preferences.addons["cycles"].preferences
        preferences.compute_device_type = "OPTIX"
        preferences.get_devices()
        used = [item for item in preferences.devices if item.type == "OPTIX"]
        for item in preferences.devices:
            item.use = item in used
        if used:
            scene.cycles.device = "GPU"
            return "OPTIX: " + ", ".join(item.name for item in used)
    except Exception as error:  # noqa: BLE001 - no OptiX: render on the CPU
        print("hardware_render: OptiX unavailable:", error)
    return "CPU"


def main():
    out, width, height, samples, files = arguments()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    meshes = load(files)
    if not meshes:
        raise SystemExit("hardware_render: the inputs hold no meshes")
    studio(scene, *bounds(meshes))
    scene.render.engine = "CYCLES"
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = width, height, 100
    scene.render.image_settings.file_format = "PNG"
    scene.cycles.samples, scene.cycles.use_denoising = samples, True
    print("hardware_render: device", device(scene), "samples", samples)
    scene.render.filepath = str(out)
    bpy.ops.render.render(write_still=True)
    print("hardware_render: wrote", out)


try:
    main()
except BaseException as error:  # a failed script must not exit 0
    import traceback

    traceback.print_exc()
    sys.exit(error.code if isinstance(error, SystemExit) and isinstance(error.code, int) else 1)
