"""Generate an original CC0 multi-file articulated fixture; no downloads or user assets."""

from pathlib import Path
import argparse
import base64


def make(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "textures").mkdir(exist_ok=True)
    # Original 1x1 PNG, deliberately small for regression/asset-path tests.
    (root / "textures" / "paint.png").write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
        )
    )
    (root / "material.usda").write_text("""#usda 1.0
 def Material "Paint" {
     token outputs:surface.connect = </Paint/Surface.outputs:surface>
     def Shader "Surface" {
         uniform token info:id = "UsdPreviewSurface"
         color3f inputs:diffuseColor = (0.12, 0.55, 0.85)
         token outputs:surface
     }
     def Shader "Texture" {
         uniform token info:id = "UsdUVTexture"
         asset inputs:file = @textures/paint.png@
     }
 }
""")
    (root / "robot.usda").write_text("""#usda 1.0
(defaultPrim = "Robot"
 metersPerUnit = 1
 upAxis = "Z")
def Xform "Robot" (prepend apiSchemas = ["PhysicsArticulationRootAPI"]) {
    def Cube "Base" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "MaterialBindingAPI"]) {
        double size = 0.5
        double3 xformOp:translate = (0, 0, 0.25)
        uniform token[] xformOpOrder = ["xformOp:translate"]
        rel material:binding = </Robot/Paint>
    }
    def Cube "Arm" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsCollisionAPI"]) {
        double size = 0.35
        double3 xformOp:translate = (0, 0, 0.8)
        uniform token[] xformOpOrder = ["xformOp:translate"]
        color3f[] primvars:displayColor = [(0.95, 0.35, 0.08)]
    }
    def PhysicsFixedJoint "Anchor" {
        rel physics:body1 = </Robot/Base>
    }
    def PhysicsRevoluteJoint "Elbow" {
        rel physics:body0 = </Robot/Base>
        rel physics:body1 = </Robot/Arm>
        uniform token physics:axis = "Y"
        point3f physics:localPos0 = (0, 0, 0.3)
        point3f physics:localPos1 = (0, 0, -0.25)
        float physics:lowerLimit = -30
        float physics:upperLimit = 30
    }
    def Material "Paint" (references = @material.usda@</Paint>) {}
}
""")
    (root / "LICENSE.txt").write_text(
        "Original test fixture dedicated to the public domain under CC0-1.0. Generated for isaac-mcp, 2026. No third-party asset content.\n"
    )
    (root / "minimal.urdf").write_text(
        '<robot name="single_link"><link name="base"><inertial><mass value="1"/><inertia ixx=".01" ixy="0" ixz="0" iyy=".01" iyz="0" izz=".01"/></inertial><visual><geometry><box size=".2 .2 .2"/></geometry></visual><collision><geometry><box size=".2 .2 .2"/></geometry></collision></link></robot>'
    )
    return root


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("output")
    make(p.parse_args().output)
