"""Scene source for the pinned RSS live scene loader."""

SCENE = '''import robosimstudio as rss

OBJECT_HALF_SIZE = (0.025, 0.025, 0.025)
OBJECT_POSE = (0.0, 0.0, 0.026)
OBJECT_MASS = 0.1
OBJECT_FRICTION = 0.4
CAMERA_EYE = (0.4, -0.6, 0.5)
CAMERA_LOOK_AT = (0.0, 0.0, 0.0)

scene = rss.Scene(cell="void", name="rigid_reference")
scene.add(rss.box(name="table", half=(0.4, 0.3, 0.02),
                  at=(0.0, 0.0, -0.02), static=True,
                  friction=OBJECT_FRICTION, color=(0.6, 0.6, 0.6)))
scene.add(rss.box(name="cube", half=OBJECT_HALF_SIZE, at=OBJECT_POSE,
                  mass=OBJECT_MASS, friction=OBJECT_FRICTION,
                  color=(0.9, 0.05, 0.05)))
scene.cameras.append(rss.CameraSpec(name="camera", width=320, height=240,
                                    eye=CAMERA_EYE, look_at=CAMERA_LOOK_AT,
                                    up=(0.0, 0.0, 1.0)))
BUILD = {"renderer": "tiled"}

def routine(sim):
    for step in range(60):
        sim.run(1)
        yield
'''
