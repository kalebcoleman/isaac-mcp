"""Lights, ground, background and the environment asset."""

import omni.kit.commands
import omni.usd
import os
import carb
from pxr import Gf
from .settings import (
    ENVIRONMENTS, ENVIRONMENT_PRIM_PATH, ENVIRONMENT_Z_OFFSETS, GRID_TINT_PRESETS, _isaac_sim_data_file,
)


class Environment:
    """Part of DigitalTwin (extension.py), which mixes it in."""

    # ==================== Lighting & Environment ====================
    def _clear_all_lights(self):
        """Remove all light prims under /World/Lights/."""
        from pxr import UsdLux
        stage = omni.usd.get_context().get_stage()
        lights_prim = stage.GetPrimAtPath("/World/Lights")
        if lights_prim and lights_prim.IsValid():
            stage.RemovePrim("/World/Lights")
        # Also remove the default dome light if it exists
        default_dome = stage.GetPrimAtPath("/World/defaultDomeLight")
        if default_dome and default_dome.IsValid():
            stage.RemovePrim("/World/defaultDomeLight")

    def _ensure_lights_scope(self):
        """Ensure /World/Lights scope exists."""
        from pxr import UsdGeom
        stage = omni.usd.get_context().get_stage()
        lights_prim = stage.GetPrimAtPath("/World/Lights")
        if not lights_prim or not lights_prim.IsValid():
            UsdGeom.Scope.Define(stage, "/World/Lights")

    def _apply_lighting_preset(self):
        """Apply the selected lighting preset, clearing previous lights first."""
        from pxr import UsdLux, UsdGeom, Gf, Sdf
        stage = omni.usd.get_context().get_stage()
        idx = self._lighting_preset_combo.model.get_item_value_model().as_int
        presets = [
            "Default Dome", "Studio (3-Point)", "Dramatic (Sun + Fill)",
            "Soft Diffuse", "HDRI: Photo Studio", "HDRI: Stormy Sky",
        ]
        preset = presets[idx]

        self._clear_all_lights()
        self._ensure_lights_scope()

        if preset == "Default Dome":
            dome = UsdLux.DomeLight.Define(stage, "/World/Lights/DomeLight")
            dome.CreateIntensityAttr().Set(1000.0)
            print("Applied preset: Default Dome (intensity=1000)")

        elif preset == "Studio (3-Point)":
            # Key light — strong, from upper-right-front
            key = UsdLux.RectLight.Define(stage, "/World/Lights/KeyLight")
            key.CreateIntensityAttr().Set(800.0)
            key.CreateWidthAttr().Set(1.0)
            key.CreateHeightAttr().Set(1.0)
            xform = UsdGeom.Xform(key.GetPrim())
            xform.ClearXformOpOrder()
            xform.AddTranslateOp().Set(Gf.Vec3d(1.5, -1.0, 2.0))
            xform.AddRotateXYZOp().Set(Gf.Vec3f(-45, 30, 0))

            # Fill light — softer, from upper-left
            fill = UsdLux.RectLight.Define(stage, "/World/Lights/FillLight")
            fill.CreateIntensityAttr().Set(300.0)
            fill.CreateWidthAttr().Set(1.5)
            fill.CreateHeightAttr().Set(1.5)
            xform = UsdGeom.Xform(fill.GetPrim())
            xform.ClearXformOpOrder()
            xform.AddTranslateOp().Set(Gf.Vec3d(-1.5, -0.5, 1.8))
            xform.AddRotateXYZOp().Set(Gf.Vec3f(-40, -30, 0))

            # Rim/back light — highlights edges from behind
            rim = UsdLux.RectLight.Define(stage, "/World/Lights/RimLight")
            rim.CreateIntensityAttr().Set(500.0)
            rim.CreateWidthAttr().Set(0.8)
            rim.CreateHeightAttr().Set(0.8)
            xform = UsdGeom.Xform(rim.GetPrim())
            xform.ClearXformOpOrder()
            xform.AddTranslateOp().Set(Gf.Vec3d(0.0, 1.5, 2.5))
            xform.AddRotateXYZOp().Set(Gf.Vec3f(-60, 180, 0))

            # Dim dome fill for ambient
            dome = UsdLux.DomeLight.Define(stage, "/World/Lights/DomeFill")
            dome.CreateIntensityAttr().Set(150.0)
            print("Applied preset: Studio (3-Point) — key/fill/rim + dim dome fill")

        elif preset == "Dramatic (Sun + Fill)":
            sun = UsdLux.DistantLight.Define(stage, "/World/Lights/SunLight")
            sun.CreateIntensityAttr().Set(3000.0)
            sun.CreateAngleAttr().Set(0.53)  # sun-sized disc
            xform = UsdGeom.Xform(sun.GetPrim())
            xform.ClearXformOpOrder()
            xform.AddRotateXYZOp().Set(Gf.Vec3f(-55, 35, 0))

            dome = UsdLux.DomeLight.Define(stage, "/World/Lights/DomeFill")
            dome.CreateIntensityAttr().Set(100.0)
            print("Applied preset: Dramatic (Sun + Fill) — strong directional + dim dome")

        elif preset == "Soft Diffuse":
            dome = UsdLux.DomeLight.Define(stage, "/World/Lights/DomeLight")
            dome.CreateIntensityAttr().Set(2500.0)
            print("Applied preset: Soft Diffuse — high-intensity dome, no directional")

        elif preset.startswith("HDRI:"):
            hdri_map = {
                "HDRI: Photo Studio": _isaac_sim_data_file(
                    "omni.kit.widget.material_preview", "data/photo_studio_01_4k.hdr"
                ),
                "HDRI: Stormy Sky": _isaac_sim_data_file(
                    "omni.kit.tool.collect",
                    "data/test_stages/OM_55150/2/Assets/Skies/2022_1/Skies/Storm/approaching_storm.hdr",
                ),
            }
            hdr_path = hdri_map.get(preset, "")
            dome = UsdLux.DomeLight.Define(stage, "/World/Lights/DomeLight")
            dome.CreateIntensityAttr().Set(1000.0)
            if os.path.exists(hdr_path):
                dome.CreateTextureFileAttr().Set(hdr_path)
                print(f"Applied preset: {preset} — HDRI dome from {os.path.basename(hdr_path)}")
            else:
                print(
                    f"Warning: HDRI file for {preset!r} not found in this Isaac Sim install"
                    f"{' (' + hdr_path + ')' if hdr_path else ''}, using plain dome instead"
                )

        # Sync slider to match new dome intensity
        self._sync_dome_slider_from_stage()

    def _sync_dome_slider_from_stage(self):
        """Update the dome intensity slider to reflect the current stage value."""
        from pxr import UsdLux
        stage = omni.usd.get_context().get_stage()
        for prim in stage.Traverse():
            if prim.IsA(UsdLux.DomeLight):
                intensity = prim.GetAttribute("inputs:intensity").Get()
                if intensity is not None:
                    self._dome_intensity_slider.model.set_value(float(intensity))
                break

    def _set_dome_intensity(self):
        """Set intensity on all dome lights in the scene."""
        from pxr import UsdLux
        stage = omni.usd.get_context().get_stage()
        val = self._dome_intensity_slider.model.as_float
        found = False
        for prim in stage.Traverse():
            if prim.IsA(UsdLux.DomeLight):
                prim.GetAttribute("inputs:intensity").Set(val)
                found = True
        if found:
            print(f"Dome light intensity set to {val}")
        else:
            print("No dome light found. Apply a lighting preset first.")

    def _set_dome_color_temp(self):
        """Set color temperature on all dome lights."""
        from pxr import UsdLux
        stage = omni.usd.get_context().get_stage()
        temp = self._dome_temp_slider.model.as_float
        enable = self._dome_temp_enable.model.get_value_as_bool()
        found = False
        for prim in stage.Traverse():
            if prim.IsA(UsdLux.DomeLight):
                prim.GetAttribute("inputs:enableColorTemperature").Set(enable)
                if enable:
                    prim.GetAttribute("inputs:colorTemperature").Set(temp)
                found = True
        if found:
            if enable:
                print(f"Dome color temperature set to {temp}K")
            else:
                print("Dome color temperature disabled (using default white)")
        else:
            print("No dome light found. Apply a lighting preset first.")

    def _add_manual_light(self):
        """Add a single light to /World/Lights/ with auto-incrementing name."""
        from pxr import UsdLux, UsdGeom, Gf
        stage = omni.usd.get_context().get_stage()
        self._ensure_lights_scope()

        idx = self._add_light_combo.model.get_item_value_model().as_int
        light_types = ["Rect Light", "Sphere Light", "Distant Light"]
        light_type = light_types[idx]
        intensity = self._add_light_intensity.model.as_float

        # Find next available index
        i = 0
        while stage.GetPrimAtPath(f"/World/Lights/Manual_{light_type.replace(' ', '')}_{i}"):
            i += 1
        path = f"/World/Lights/Manual_{light_type.replace(' ', '')}_{i}"

        if light_type == "Rect Light":
            light = UsdLux.RectLight.Define(stage, path)
            light.CreateWidthAttr().Set(1.0)
            light.CreateHeightAttr().Set(1.0)
        elif light_type == "Sphere Light":
            light = UsdLux.SphereLight.Define(stage, path)
            light.CreateRadiusAttr().Set(0.2)
        elif light_type == "Distant Light":
            light = UsdLux.DistantLight.Define(stage, path)
            light.CreateAngleAttr().Set(0.53)

        light.CreateIntensityAttr().Set(intensity)

        # Place it above the scene so it's immediately useful
        xform = UsdGeom.Xform(light.GetPrim())
        xform.ClearXformOpOrder()
        xform.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, 2.5))
        xform.AddRotateXYZOp().Set(Gf.Vec3f(-90, 0, 0))

        print(f"Added {light_type} at {path} (intensity={intensity}). Move it in the viewport to position.")

    def _remove_extra_lights(self):
        """Remove all manually added lights (under /World/Lights/Manual_*)."""
        from pxr import Usd
        stage = omni.usd.get_context().get_stage()
        lights_prim = stage.GetPrimAtPath("/World/Lights")
        if not lights_prim or not lights_prim.IsValid():
            print("No extra lights to remove.")
            return
        removed = 0
        for child in lights_prim.GetChildren():
            if child.GetName().startswith("Manual_"):
                stage.RemovePrim(child.GetPath())
                removed += 1
        print(f"Removed {removed} manual light(s).")

    _ground_mat_path = None  # cached path for the OmniPBR ground material

    def _apply_dark_studio_ground(self):
        """Quick shortcut: apply Dark Studio ground style with default roughness."""
        self._ground_style_combo.model.get_item_value_model().set_value(2)  # "Dark Studio"
        self._ground_roughness_slider.model.set_value(0.5)
        self._apply_ground_style()

    def _load_environment(self):
        """Load a full-scene environment USD (walls, floor, ceiling) as a reference at
        /World/Environment. Pauses timeline during swap to avoid PhysX tensor invalidation,
        optionally hides the flat /World/defaultGroundPlane to prevent Z-fighting."""
        from pxr import UsdGeom, Sdf
        import omni.timeline
        from isaacsim.storage.native import get_assets_root_path
        from isaacsim.core.utils.stage import add_reference_to_stage

        stage = omni.usd.get_context().get_stage()
        idx = self._env_combo.model.get_item_value_model().as_int
        names = list(ENVIRONMENTS.keys())
        name = names[idx]
        rel_path = ENVIRONMENTS[name]

        timeline = omni.timeline.get_timeline_interface()
        was_playing = timeline.is_playing()
        if was_playing:
            timeline.pause()

        # Remove existing environment reference if present
        existing = stage.GetPrimAtPath(ENVIRONMENT_PRIM_PATH)
        if existing and existing.IsValid():
            stage.RemovePrim(ENVIRONMENT_PRIM_PATH)
            print(f"Removed existing environment at {ENVIRONMENT_PRIM_PATH}")

        if rel_path is None:
            # "None" option: also un-hide flat ground
            gp = stage.GetPrimAtPath("/World/defaultGroundPlane")
            if gp and gp.IsValid():
                UsdGeom.Imageable(gp).MakeVisible()
            print("Environment cleared (flat ground plane restored)")
            if was_playing:
                timeline.play()
            return

        root = get_assets_root_path()
        if not root:
            print("Error: Isaac assets root not found")
            return
        full_path = root + "/" + rel_path

        try:
            add_reference_to_stage(usd_path=full_path, prim_path=ENVIRONMENT_PRIM_PATH)
            print(f"Loaded environment: {name} ({full_path})")
        except Exception as e:
            print(f"Error loading environment {name}: {e}")
            if was_playing:
                timeline.play()
            return

        # Apply per-env Z offset to align floor surface with z=0
        z_off = ENVIRONMENT_Z_OFFSETS.get(name, 0.0)
        if z_off != 0.0:
            env_prim = stage.GetPrimAtPath(ENVIRONMENT_PRIM_PATH)
            xform = UsdGeom.Xformable(env_prim)
            xform.ClearXformOpOrder()
            xform.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, z_off))
            print(f"Applied env z-offset: {z_off:+.3f} m")

        # Hide flat ground plane if checkbox set
        if self._env_hide_ground.model.get_value_as_bool():
            gp = stage.GetPrimAtPath("/World/defaultGroundPlane")
            if gp and gp.IsValid():
                UsdGeom.Imageable(gp).MakeInvisible()
                print("Flat ground plane hidden")

        if was_playing:
            timeline.play()

    def _swap_ground_plane(self):
        """Delete and recreate /World/defaultGroundPlane with a new grid tint color.
        Pauses timeline during swap so PhysX can release the old rigid body cleanly."""
        from pxr import Gf
        import omni.timeline
        from isaacsim.core.api.objects import GroundPlane

        stage = omni.usd.get_context().get_stage()

        # Resolve tint color
        idx = self._grid_tint_combo.model.get_item_value_model().as_int
        names = list(GRID_TINT_PRESETS.keys())
        name = names[idx]
        if name == "Custom Hex":
            hex_str = self._grid_tint_hex.model.get_value_as_string().strip().lstrip("#")
            try:
                if len(hex_str) != 6:
                    raise ValueError("expected 6 hex digits")
                r = int(hex_str[0:2], 16) / 255.0
                g = int(hex_str[2:4], 16) / 255.0
                b = int(hex_str[4:6], 16) / 255.0
                color = (r, g, b)
            except Exception as e:
                print(f"Invalid hex '{hex_str}': {e}. Falling back to white.")
                color = (1.0, 1.0, 1.0)
        else:
            color = GRID_TINT_PRESETS[name]

        timeline = omni.timeline.get_timeline_interface()
        was_playing = timeline.is_playing()
        if was_playing:
            timeline.pause()

        # Delete existing ground plane
        gp_prim = stage.GetPrimAtPath("/World/defaultGroundPlane")
        if gp_prim and gp_prim.IsValid():
            stage.RemovePrim("/World/defaultGroundPlane")
            print("Removed existing ground plane")

        # Clear cached material path — old material prim is gone with the ground
        type(self)._ground_mat_path = None

        try:
            GroundPlane(
                prim_path="/World/defaultGroundPlane",
                z_position=0.0,
                size=5000,
                color=Gf.Vec3f(*color),
            )
            print(f"Recreated ground plane with tint {name} (rgb={color})")
        except Exception as e:
            print(f"Error recreating ground plane: {e}")

        if was_playing:
            timeline.play()

    def _apply_ground_style(self):
        """Apply a ground plane visual style by creating an OmniPBR material and binding
        it at the Xform root of the ground plane (where the default binding lives)."""
        from pxr import UsdGeom, Gf, UsdShade
        stage = omni.usd.get_context().get_stage()

        idx = self._ground_style_combo.model.get_item_value_model().as_int
        styles = ["Default Grey", "White Studio", "Dark Studio", "Concrete", "Invisible (Physics Only)"]
        style = styles[idx]
        roughness = self._ground_roughness_slider.model.as_float

        ground_prim = stage.GetPrimAtPath("/World/defaultGroundPlane")
        if not ground_prim or not ground_prim.IsValid():
            print("No ground plane found. Load the scene first.")
            return

        if style == "Invisible (Physics Only)":
            UsdGeom.Imageable(ground_prim).MakeInvisible()
            print("Ground plane hidden (physics still active)")
            return
        else:
            UsdGeom.Imageable(ground_prim).MakeVisible()

        # Default Grey: restore the original UsdPreviewSurface material
        if style == "Default Grey":
            orig_mat = stage.GetPrimAtPath("/World/Looks/visual_material")
            if orig_mat and orig_mat.IsValid():
                UsdShade.MaterialBindingAPI.Apply(ground_prim)
                UsdShade.MaterialBindingAPI(ground_prim).Bind(UsdShade.Material(orig_mat))
                print("Applied ground style: Default Grey (restored original material)")
            else:
                print("Warning: Original visual_material not found")
            return

        color_map = {
            "White Studio": (0.95, 0.95, 0.95),
            "Dark Studio": (0.1, 0.1, 0.1),
            "Concrete": (0.6, 0.58, 0.55),
        }
        color = color_map.get(style, (0.5, 0.5, 0.5))

        # Reuse cached material if it still exists on stage
        mat_path = type(self)._ground_mat_path
        mat_prim = stage.GetPrimAtPath(mat_path) if mat_path else None
        if not mat_prim or not mat_prim.IsValid():
            # CreateAndBindMdlMaterialFromLibrary creates at /Looks/ (root scope)
            out = []
            omni.kit.commands.execute(
                "CreateAndBindMdlMaterialFromLibrary",
                mdl_name="OmniPBR.mdl",
                mtl_name="OmniPBR",
                mtl_created_list=out,
                prim_name="GroundMaterial",
                select_new_prim=False,
            )
            if out:
                mat_path = out[0]
                type(self)._ground_mat_path = mat_path
                print(f"Created OmniPBR ground material at {mat_path}")
            mat_prim = stage.GetPrimAtPath(mat_path) if mat_path else None

        if not mat_prim or not mat_prim.IsValid():
            print("Error: Failed to create ground material.")
            return

        # Set shader properties — MDL attributes must be created if they don't exist yet
        from pxr import Sdf
        shader_prim = stage.GetPrimAtPath(f"{mat_path}/Shader")
        if shader_prim and shader_prim.IsValid():
            color_attr = shader_prim.GetAttribute("inputs:diffuse_color_constant")
            if not color_attr or not color_attr.IsValid():
                color_attr = shader_prim.CreateAttribute("inputs:diffuse_color_constant", Sdf.ValueTypeNames.Color3f)
            color_attr.Set(Gf.Vec3f(*color))

            rough_attr = shader_prim.GetAttribute("inputs:reflection_roughness_constant")
            if not rough_attr or not rough_attr.IsValid():
                rough_attr = shader_prim.CreateAttribute("inputs:reflection_roughness_constant", Sdf.ValueTypeNames.Float)
            rough_attr.Set(roughness)

            tex_attr = shader_prim.GetAttribute("inputs:diffuse_texture")
            if tex_attr and tex_attr.IsValid():
                tex_attr.Clear()
        else:
            print(f"Warning: No shader found at {mat_path}/Shader")

        # Bind at the Xform root (overrides the default visual_material binding)
        UsdShade.MaterialBindingAPI.Apply(ground_prim)
        UsdShade.MaterialBindingAPI(ground_prim).Bind(UsdShade.Material(mat_prim))

        print(f"Applied ground style: {style} (color={color}, roughness={roughness:.2f})")

    def _apply_background(self):
        """Set the viewport background. Solid colors use RTX settings; HDRI comes from dome light."""
        idx = self._bg_combo.model.get_item_value_model().as_int
        options = ["From Dome Light (HDRI/Default)", "Solid White", "Solid Black", "Solid Light Grey"]
        choice = options[idx]

        settings = carb.settings.get_settings()

        if choice == "From Dome Light (HDRI/Default)":
            # Restore default — dome light controls the background
            settings.set("/rtx/background/enabled", False)
            print("Background: controlled by dome light / HDRI")
        else:
            color_map = {
                "Solid White": (1.0, 1.0, 1.0),
                "Solid Black": (0.0, 0.0, 0.0),
                "Solid Light Grey": (0.85, 0.85, 0.85),
            }
            r, g, b = color_map[choice]
            # Use the dome light approach: set a flat-color dome
            from pxr import UsdLux
            stage = omni.usd.get_context().get_stage()
            # Find existing dome light
            dome_prim = None
            for prim in stage.Traverse():
                if prim.IsA(UsdLux.DomeLight):
                    dome_prim = prim
                    break
            if dome_prim:
                # Clear HDRI texture so the color attribute controls appearance
                tex_attr = dome_prim.GetAttribute("inputs:texture:file")
                if tex_attr and tex_attr.IsValid():
                    tex_attr.Clear()
                from pxr import Gf
                dome_prim.GetAttribute("inputs:color").Set(Gf.Vec3f(r, g, b))
                print(f"Background: {choice} (dome light color set, HDRI cleared)")
            else:
                print("No dome light found. Apply a lighting preset first, then set background.")

    def _reset_lighting_defaults(self):
        """Reset to default: single dome light at 1000, default ground, default background."""
        self._clear_all_lights()
        from pxr import UsdLux, UsdGeom
        stage = omni.usd.get_context().get_stage()

        # Restore default dome light
        dome = UsdLux.DomeLight.Define(stage, "/World/defaultDomeLight")
        dome.CreateIntensityAttr().Set(1000.0)

        # Make ground visible again
        ground_prim = stage.GetPrimAtPath("/World/defaultGroundPlane")
        if ground_prim and ground_prim.IsValid():
            UsdGeom.Imageable(ground_prim).MakeVisible()

        # Reset background
        settings = carb.settings.get_settings()
        settings.set("/rtx/background/enabled", False)

        # Sync UI
        self._dome_intensity_slider.model.set_value(1000.0)
        self._dome_temp_slider.model.set_value(6500.0)
        self._dome_temp_enable.model.set_value(False)
        self._lighting_preset_combo.model.get_item_value_model().set_value(0)
        self._ground_style_combo.model.get_item_value_model().set_value(0)
        self._ground_roughness_slider.model.set_value(0.5)
        self._bg_combo.model.get_item_value_model().set_value(0)

        print("Reset lighting, ground, and background to defaults.")
