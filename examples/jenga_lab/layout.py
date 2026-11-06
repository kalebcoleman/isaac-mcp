"""Where everything is. No Isaac Lab imports, so the tests can check it without a simulator.

The same blocks, rod and arm pose as ``sim__quick_start environment=jenga``, with fewer layers so that thousands of environments fit a
16 GB GPU: 6 layers instead of 18, on a taller table so the block to push stays at the height where the arm can hold its hand level.
"""

BLOCK = (0.15, 0.05, 0.03)  # length, width, height, metres
BLOCK_MASS = 0.12
FRICTION = (0.5, 0.4)
SIDE_GAP = 0.0005
LAYERS = 6
TARGET_LAYER = 3  # an odd layer runs along x, towards the arm
PUSH_Z = 0.625
TABLE_TOP = PUSH_Z - (TARGET_LAYER + 0.5) * BLOCK[2]
TABLE_CENTER_X, TABLE_SIZE = 0.85, (0.6, 1.0)  # starts at x=0.55: at this height a longer table would hold up the wrist, which hangs below the hand
TOWER_XY = (0.7, 0.0)
READY_JOINTS = {"panda_joint1": 0.0, "panda_joint2": -0.8758, "panda_joint3": 0.0, "panda_joint4": -2.8161, "panda_joint5": 0.0,
                "panda_joint6": 3.511, "panda_joint7": 0.785, "panda_finger_joint.*": 0.02}
HAND_LINK = "panda_hand"
ROD_WIDTH, ROD_FROM, ROD_TIP = 0.012, 0.05, 0.26  # along the hand's z axis
HAND_LEVEL_XYZW = (0.7071068, 0.0, 0.7071068, 0.0)  # hand z along world +x
TIP_START = (0.565, 0.0, PUSH_Z)
TIP_MIN, TIP_MAX = (0.50, -0.04, PUSH_Z - 0.04), (0.76, 0.04, PUSH_Z + 0.04)  # where the policy may send the rod's tip


def block_name(layer: int, index: int) -> str:
    return f"L{layer:02d}_B{index}"


def blocks():
    """``(name, position, size)`` of every block, bottom layer first."""
    length, width, height = BLOCK
    step = width + SIDE_GAP
    for layer in range(LAYERS):
        along_x = layer % 2 == 1
        z = TABLE_TOP + (layer + 0.5) * height
        for index in range(3):
            offset = (index - 1) * step
            position = (TOWER_XY[0], TOWER_XY[1] + offset, z) if along_x else (TOWER_XY[0] + offset, TOWER_XY[1], z)
            yield block_name(layer, index), position, (length, width, height) if along_x else (width, length, height)


TARGET = block_name(TARGET_LAYER, 1)
