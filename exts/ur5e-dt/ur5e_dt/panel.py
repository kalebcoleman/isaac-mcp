"""The twin's panel: its window, the scene controls and the object visibility list."""

import omni.ui as ui
import asyncio
import carb
from .settings import ASSEMBLIES, ENVIRONMENTS, GRID_TINT_PRESETS


class Panel:
    """Part of DigitalTwin (extension.py), which mixes it in."""

    def _focus_panel_soon(self):
        """Show and focus the panel once its deferred docking has run.

        A newly created window can open behind another tab of its dock. Focusing it is the same
        call sim__ui__act action=tab makes, and it is skipped if anything goes wrong: the panel is
        still usable from the Window menu or by that tool.
        """
        async def focus():
            try:
                import omni.kit.app

                app = omni.kit.app.get_app()
                for _ in range(4):
                    await app.next_update_async()
                    window = self._window
                    if window is None or not window.visible:
                        return
                    ui.Workspace.show_window("UR5e Digital Twin", True)
                    window.focus()
                    if not window.docked or window.is_selected_in_dock():
                        return
            except Exception as exc:  # noqa: BLE001 - a hidden tab is cosmetic, never a startup failure
                carb.log_warn(f"[DigitalTwin] could not focus the twin panel: {exc}")

        try:
            asyncio.ensure_future(focus())
        except Exception as exc:  # noqa: BLE001
            carb.log_warn(f"[DigitalTwin] could not schedule the twin panel focus: {exc}")

    def create_ui(self):
        with ui.VStack(spacing=5):
            self._sim_setup_collapse = ui.CollapsableFrame(title="Simulation Setup", collapsed=False, height=0)
            self._sim_setup_collapse.set_collapsed_changed_fn(
                lambda collapsed: None if collapsed else self._refresh_viewport_camera_list()
            )
            with self._sim_setup_collapse:
                with ui.VStack(spacing=5, height=0):
                    with ui.HStack(spacing=5):
                        ui.Button("Load Scene", width=100, height=35, clicked_fn=lambda: asyncio.ensure_future(self.load_scene()))
                        self._viewport_toggle_btn = ui.Button("Disable Viewport", width=140, height=35, clicked_fn=self._toggle_viewport_rendering)
                        ui.Button("Quick Start", width=120, height=35, clicked_fn=lambda: asyncio.ensure_future(self.quick_start()))
                    with ui.HStack(spacing=5):
                        ui.Button("Load Workstation", width=140, height=35,
                                  clicked_fn=lambda: asyncio.ensure_future(self.load_workstation()),
                                  tooltip="Add the Vention bench under the robot (tabletop top at z=0; robot/objects unchanged). Hides the grid visual.")
                        ui.Button("Import Simple Room", width=160, height=35,
                                  clicked_fn=lambda: asyncio.ensure_future(self.load_simple_room()),
                                  tooltip="Add Isaac's Simple Room backdrop (floor/walls/window) at z=-0.11. Hides the grid visual.")
                    with ui.HStack(spacing=5):
                        ui.Button("Quick Start with Workstation", width=210, height=35,
                                  clicked_fn=lambda: asyncio.ensure_future(self.quick_start_with_workstation()),
                                  tooltip="Run Quick Start, then load the Vention workstation bench (your own lab file; a placeholder is used without it).")
                        ui.Button("Quick Start with Workstation + Simple Room", width=320, height=35,
                                  clicked_fn=lambda: asyncio.ensure_future(self.quick_start_with_workstation_and_simple_room()),
                                  tooltip="Run Quick Start, then load the Vention bench AND the Simple Room backdrop.")
                    with ui.HStack(spacing=5):
                        ui.Button("Dark Studio Ground", width=160, height=30, clicked_fn=self._apply_dark_studio_ground)
                    with ui.HStack(spacing=5):
                        self._viewport_pub_checkbox = ui.CheckBox(width=20)
                        self._viewport_pub_checkbox.model.set_value(True)
                        ui.Label("Viewport Publisher (default, zero GPU cost)", alignment=ui.Alignment.LEFT)
                    with ui.HStack(spacing=5):
                        ui.Label("Active Viewport:", alignment=ui.Alignment.LEFT, width=110)
                        self._viewport_cam_frame = ui.Frame(width=220)
                        with self._viewport_cam_frame:
                            self._viewport_cam_combo = ui.ComboBox(0, "/World/workspace_camera_sim", width=220)
                        ui.Button("Undo", width=55, height=22, clicked_fn=self._undo_viewport_camera, tooltip="Switch back to the previously active viewport camera")
                    # Auto-populate the dropdown with current stage cameras at build time
                    self._refresh_viewport_camera_list()

            with ui.CollapsableFrame(title="UR5e Control", collapsed=False, height=0):
                with ui.VStack(spacing=5, height=0):
                    with ui.HStack(spacing=5):
                        ui.Button("Import UR5e", width=100, height=35, clicked_fn=lambda: asyncio.ensure_future(self.load_ur5e()))
                        ui.Button("Setup UR5e Action Graph", width=200, height=35, clicked_fn=lambda: asyncio.ensure_future(self.setup_action_graph()))
                        ui.Button("Setup UR5e Force Publisher", width=200, height=35, clicked_fn=self.setup_force_publish_action_graph)

            with ui.CollapsableFrame(title="RG2 Gripper", collapsed=False, height=0):
                with ui.VStack(spacing=5, height=0):
                    with ui.HStack(spacing=5):
                        ui.Button("Import RG2 Gripper", width=150, height=35, clicked_fn=self.import_rg2_gripper)
                        ui.Button("Attach Gripper to UR5e", width=180, height=35, clicked_fn=self.attach_rg2_to_ur5e)
                        ui.Button("Setup Gripper Action Graph", width=200, height=35, clicked_fn=self.setup_gripper_action_graph)

            # Intel RealSense Camera
            with ui.CollapsableFrame(title="Intel RealSense Camera", collapsed=False, height=0):
                with ui.VStack(spacing=5, height=0):
                    with ui.HStack(spacing=5):
                        ui.Button("Import RealSense Camera", width=170, height=35, clicked_fn=self.import_realsense_camera)
                        ui.Button("Attach Camera to UR5e", width=160, height=35, clicked_fn=self.attach_camera_to_ur5e)
                        ui.Button("Setup Camera Action Graph", width=200, height=35, clicked_fn=self.setup_camera_action_graph)

            with ui.CollapsableFrame(title="Objects", collapsed=False, height=0):
                with ui.VStack(spacing=5, height=0):
                    with ui.CollapsableFrame(title="Import", name="subFrame", collapsed=False, height=0):
                        with ui.VStack(spacing=5, height=0):
                            with ui.HStack(spacing=5):
                                ui.Label("Assembly:", alignment=ui.Alignment.LEFT, width=100)
                                assembly_names = list(ASSEMBLIES.keys())
                                self._assembly_combo = ui.ComboBox(0, *assembly_names, width=150)
                                self._assembly_combo.model.add_item_changed_fn(
                                    lambda m, _: self._on_assembly_changed(m)
                                )
                            with ui.HStack(spacing=5):
                                ui.Button("Add Objects", width=150, height=35, clicked_fn=self.add_objects)
                                ui.Button("Add ArUco Markers", width=180, height=35, clicked_fn=self.add_aruco_markers)
                            with ui.HStack(spacing=5):
                                ui.Button("Setup Pose Publisher", width=180, height=35, clicked_fn=self.create_pose_publisher)
                                ui.Button("Delete Objects", width=150, height=35, clicked_fn=self.delete_objects)
                    with ui.CollapsableFrame(title="Scene State", name="subFrame", collapsed=True, height=0):
                        with ui.VStack(spacing=5, height=0):
                            with ui.HStack(spacing=5):
                                ui.Button("Sync Real Poses", width=180, height=35, clicked_fn=self.sync_real_poses)
                            with ui.HStack(spacing=5):
                                ui.Button("Assemble", width=120, height=35, clicked_fn=self.assemble_objects)
                                ui.Button("Disassemble", width=120, height=35, clicked_fn=self.disassemble_objects)
                                ui.Button("Randomize Poses", width=150, height=35, clicked_fn=self.randomize_object_poses)
                            with ui.HStack(spacing=5):
                                ui.Button("Save State", width=120, height=35,
                                    clicked_fn=lambda: self._cmd_save_scene_state())
                                ui.Button("Restore State", width=120, height=35,
                                    clicked_fn=lambda: self._cmd_restore_scene_state())
                            with ui.CollapsableFrame(title="Exclude / Include Objects", collapsed=True, height=0):
                                self._visibility_frame = ui.Frame(build_fn=self._build_visibility_ui)

            # Additional Camera
            with ui.CollapsableFrame(title="Additional Camera", collapsed=True, height=0):
                with ui.VStack(spacing=5, height=0):
                    ui.Label("Camera Configuration", alignment=ui.Alignment.LEFT)
                    with ui.VStack(spacing=5):
                        with ui.HStack(spacing=5):
                            self._workspace_checkbox = ui.CheckBox(width=20)
                            self._workspace_checkbox.model.set_value(False)
                            ui.Label("Workspace Camera", alignment=ui.Alignment.LEFT, width=120)
                        with ui.HStack(spacing=5):
                            self._custom_checkbox = ui.CheckBox(width=20)
                            ui.Label("Custom Camera", alignment=ui.Alignment.LEFT, width=120)
                    with ui.HStack(spacing=5):
                        ui.Label("Camera Prim Path:", alignment=ui.Alignment.LEFT, width=120)
                        self._custom_camera_prim_field = ui.StringField(width=200)
                        self._custom_camera_prim_field.model.set_value("/World/custom_camera")
                    with ui.HStack(spacing=5):
                        ui.Label("ROS2 Topic Name:", alignment=ui.Alignment.LEFT, width=120)
                        self._custom_camera_topic_field = ui.StringField(width=200)
                        self._custom_camera_topic_field.model.set_value("custom_camera")
                    with ui.HStack(spacing=5):
                        ui.Label("Resolution:", alignment=ui.Alignment.LEFT, width=120)
                        self._resolution_combo = ui.ComboBox(0, "640x480", "1280x720", "1920x1080", width=150)
                    with ui.HStack(spacing=5):
                        ui.Button("Create Camera", width=150, height=35, clicked_fn=self.create_additional_camera)
                        ui.Button("Create Action Graph", width=180, height=35, clicked_fn=self.create_additional_camera_actiongraph)

            # Lighting & Environment
            with ui.CollapsableFrame(title="Lighting & Environment", collapsed=True, height=0):
                with ui.VStack(spacing=5, height=0):
                    with ui.CollapsableFrame(title="Lighting Preset", name="subFrame", collapsed=False, height=0):
                        with ui.VStack(spacing=5, height=0):
                            with ui.HStack(spacing=5):
                                ui.Label("Preset:", alignment=ui.Alignment.LEFT, width=80)
                                self._lighting_preset_combo = ui.ComboBox(
                                    0, "Default Dome", "Studio (3-Point)", "Dramatic (Sun + Fill)",
                                    "Soft Diffuse", "HDRI: Photo Studio", "HDRI: Stormy Sky",
                                    width=200,
                                )
                            ui.Button("Apply Lighting Preset", height=35, clicked_fn=self._apply_lighting_preset)
                    with ui.CollapsableFrame(title="Manual Light Controls", name="subFrame", collapsed=True, height=0):
                        with ui.VStack(spacing=5, height=0):
                            with ui.HStack(spacing=5):
                                ui.Label("Dome Intensity:", alignment=ui.Alignment.LEFT, width=120)
                                self._dome_intensity_slider = ui.FloatSlider(min=0, max=5000, step=50, width=200)
                                self._dome_intensity_slider.model.set_value(1000.0)
                            ui.Button("Set Dome Intensity", height=30, clicked_fn=self._set_dome_intensity)
                            with ui.HStack(spacing=5):
                                ui.Label("Color Temp (K):", alignment=ui.Alignment.LEFT, width=120)
                                self._dome_temp_slider = ui.FloatSlider(min=2000, max=10000, step=100, width=200)
                                self._dome_temp_slider.model.set_value(6500.0)
                            with ui.HStack(spacing=5):
                                self._dome_temp_enable = ui.CheckBox(width=20)
                                self._dome_temp_enable.model.set_value(False)
                                ui.Label("Enable Color Temperature", alignment=ui.Alignment.LEFT)
                            ui.Button("Set Color Temperature", height=30, clicked_fn=self._set_dome_color_temp)
                            ui.Separator(height=5)
                            with ui.HStack(spacing=5):
                                ui.Label("Add Light:", alignment=ui.Alignment.LEFT, width=80)
                                self._add_light_combo = ui.ComboBox(0, "Rect Light", "Sphere Light", "Distant Light", width=150)
                            with ui.HStack(spacing=5):
                                ui.Label("Intensity:", alignment=ui.Alignment.LEFT, width=80)
                                self._add_light_intensity = ui.FloatField(width=100)
                                self._add_light_intensity.model.set_value(500.0)
                            ui.Button("Add Light", height=30, clicked_fn=self._add_manual_light)
                            ui.Button("Remove All Extra Lights", height=30, clicked_fn=self._remove_extra_lights)
                    with ui.CollapsableFrame(title="Environment", name="subFrame", collapsed=False, height=0):
                        with ui.VStack(spacing=5, height=0):
                            with ui.HStack(spacing=5):
                                ui.Label("Preset:", alignment=ui.Alignment.LEFT, width=100)
                                self._env_combo = ui.ComboBox(
                                    0, *list(ENVIRONMENTS.keys()), width=250,
                                )
                            with ui.HStack(spacing=5):
                                ui.Spacer(width=100)
                                self._env_hide_ground = ui.CheckBox(width=20)
                                self._env_hide_ground.model.set_value(True)
                                ui.Label("Hide flat ground plane when loading", alignment=ui.Alignment.LEFT)
                            ui.Button("Load Environment", height=35, clicked_fn=self._load_environment)
                    with ui.CollapsableFrame(title="Ground Plane", name="subFrame", collapsed=False, height=0):
                        with ui.VStack(spacing=5, height=0):
                            with ui.HStack(spacing=5):
                                ui.Label("Grid Tint:", alignment=ui.Alignment.LEFT, width=100)
                                self._grid_tint_combo = ui.ComboBox(
                                    0, *list(GRID_TINT_PRESETS.keys()), width=150,
                                )
                            with ui.HStack(spacing=5):
                                ui.Label("Custom Hex:", alignment=ui.Alignment.LEFT, width=100)
                                self._grid_tint_hex = ui.StringField(width=150)
                                self._grid_tint_hex.model.set_value("#FFFFFF")
                            ui.Label("Hex used only when 'Custom Hex' is selected", style={"color": 0xFF888888, "font_size": 11}, height=0)
                            ui.Button("Swap Ground Plane", height=35, clicked_fn=self._swap_ground_plane)
                    with ui.CollapsableFrame(title="Ground Appearance", name="subFrame", collapsed=True, height=0):
                        with ui.VStack(spacing=5, height=0):
                            with ui.HStack(spacing=5):
                                ui.Label("Ground Style:", alignment=ui.Alignment.LEFT, width=100)
                                self._ground_style_combo = ui.ComboBox(
                                    0, "Default Grey", "White Studio", "Dark Studio",
                                    "Concrete", "Invisible (Physics Only)",
                                    width=200,
                                )
                            with ui.HStack(spacing=5):
                                ui.Label("Roughness:", alignment=ui.Alignment.LEFT, width=100)
                                self._ground_roughness_slider = ui.FloatSlider(min=0.0, max=1.0, step=0.05, width=200)
                                self._ground_roughness_slider.model.set_value(0.5)
                            ui.Button("Apply Ground Style", height=35, clicked_fn=self._apply_ground_style)
                    with ui.CollapsableFrame(title="Background", name="subFrame", collapsed=True, height=0):
                        with ui.VStack(spacing=5, height=0):
                            with ui.HStack(spacing=5):
                                ui.Label("Background:", alignment=ui.Alignment.LEFT, width=100)
                                self._bg_combo = ui.ComboBox(
                                    0, "From Dome Light (HDRI/Default)", "Solid White",
                                    "Solid Black", "Solid Light Grey",
                                    width=250,
                                )
                            ui.Button("Apply Background", height=35, clicked_fn=self._apply_background)
                    ui.Separator(height=5)
                    ui.Button("Reset to Defaults", height=30, clicked_fn=self._reset_lighting_defaults)

            # Video Recording
            self._rec_collapse = ui.CollapsableFrame(title="Video Recording", collapsed=True, height=0)
            self._rec_collapse.set_collapsed_changed_fn(lambda collapsed: None if collapsed else self._refresh_camera_list())
            with self._rec_collapse:
                with ui.VStack(spacing=5, height=0):
                    with ui.HStack(spacing=5):
                        ui.Label("Camera:", alignment=ui.Alignment.LEFT, width=80)
                        self._rec_camera_frame = ui.Frame(width=250)
                        with self._rec_camera_frame:
                            self._rec_camera_combo = ui.ComboBox(0, "Active Viewport", width=250)
                    with ui.HStack(spacing=5):
                        ui.Label("Filename:", alignment=ui.Alignment.LEFT, width=80)
                        self._rec_filename_field = ui.StringField(width=250)
                        self._rec_filename_field.model.set_value("")
                    ui.Label("Leave empty for auto: isaacsim_YYYY-MM-DD_HH-MM-SS.mp4", style={"color": 0xFF888888, "font_size": 11}, height=0)
                    with ui.HStack(spacing=5):
                        ui.Label("Duration (s):", alignment=ui.Alignment.LEFT, width=80)
                        self._rec_duration_field = ui.FloatField(width=80)
                        self._rec_duration_field.model.set_value(0.0)
                    ui.Label("0 = manual stop (ffmpeg only). Per-physics-step always uses this duration.", style={"color": 0xFF888888, "font_size": 11}, height=0)
                    with ui.CollapsableFrame(title="Real-Time Recording (ffmpeg)", name="subFrame", collapsed=False, height=0):
                        with ui.VStack(spacing=5, height=0):
                            with ui.HStack(spacing=5):
                                self._rt_record_btn = ui.Button("Start Recording", width=150, height=35, clicked_fn=self._start_rt_from_ui)
                                self._rt_stop_btn = ui.Button("Stop Recording", width=150, height=35, clicked_fn=self._stop_realtime_recording, enabled=False)
                    with ui.CollapsableFrame(title="Per-Physics-Step Recording (no dropped frames)", name="subFrame", collapsed=True, height=0):
                        with ui.VStack(spacing=5, height=0):
                            with ui.HStack(spacing=5):
                                ui.Label("Resolution:", alignment=ui.Alignment.LEFT, width=100)
                                self._offline_res_combo = ui.ComboBox(2, "640x480", "1280x720", "1920x1080", "2560x1440", "3840x2160", width=150)
                            with ui.HStack(spacing=5):
                                ui.Label("FPS:", alignment=ui.Alignment.LEFT, width=100)
                                self._offline_fps_field = ui.IntField(width=80)
                                self._offline_fps_field.model.set_value(30)
                            ui.Button("Record", height=35, clicked_fn=lambda: asyncio.ensure_future(self._record_offline_from_ui()))
                    self._last_video_label = ui.Label("", alignment=ui.Alignment.LEFT, word_wrap=True, height=0)
                    ui.Button("Open Videos Folder", height=25, clicked_fn=self._open_videos_folder)

            with ui.CollapsableFrame(title="Logs", collapsed=True, height=0):
                with ui.VStack(spacing=3, height=0):
                    self._log_tab_style = {
                        "RadioButton": {
                            "margin": 2, "border_radius": 2, "font_size": 16,
                            "background_color": 0xFF212121, "color": 0xFF444444,
                        },
                        "RadioButton.Label": {"font_size": 16, "color": 0xFF777777},
                        "RadioButton:checked": {"background_color": 0xFF3A3A3A, "color": 0xFF222222},
                        "RadioButton.Label:checked": {"color": 0xFFCCCCCC},
                    }
                    self._log_tab_collection = ui.RadioCollection()
                    with ui.HStack(spacing=3, height=30, style=self._log_tab_style):
                        ui.RadioButton(text="Extension", width=80,
                            radio_collection=self._log_tab_collection,
                            clicked_fn=lambda: self._switch_log_tab("extension"))
                        ui.RadioButton(text="System", width=80,
                            radio_collection=self._log_tab_collection,
                            clicked_fn=lambda: self._switch_log_tab("system"))
                    self._log_stack = ui.ZStack(height=100)
                    with self._log_stack:
                        self._ext_log_scroll = ui.ScrollingFrame(
                            horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                            vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_ON,
                            style={"background_color": 0xFF24211F},
                        )
                        with self._ext_log_scroll:
                            self._ext_log_vstack = ui.VStack(spacing=0)
                        self._sys_log_scroll = ui.ScrollingFrame(
                            horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                            vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_ON,
                            visible=False,
                            style={"background_color": 0xFF24211F},
                        )
                        with self._sys_log_scroll:
                            self._sys_log_vstack = ui.VStack(spacing=0)
                    with ui.HStack(spacing=5):
                        ui.Button("Copy Logs", width=120, height=30, clicked_fn=self._copy_logs)
                        ui.Button("Clear Logs", width=120, height=30, clicked_fn=self._clear_logs)

        self._active_log_tab = "extension"
        self._ext_log_lines = []
        self._sys_log_lines = []
        self._log_dirty = False
        # One per-frame, main-thread drain of the log buffers into the UI panels (see
        # _append_*/_drain_log_buffer). Subscribed BEFORE _setup_log_redirect installs the producers
        # so the very first captured line is drained safely on the next frame, never inline.
        try:
            import omni.kit.app
            self._log_drain_sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
                self._drain_log_buffer, name="ur5e_dt_log_drain")
        except Exception:
            self._log_drain_sub = None
        # The extension can be enabled from run_python, which temporarily captures
        # sys.stdout. Install the redirector on the next tick, after that capture
        # restores its stream, so a reload cannot retain a retired instance.
        self._run_on_main_thread(self._setup_log_redirect)

        # Re-subscribe physics callbacks if action graphs already exist (e.g. after hot-reload)
        self._resubscribe_physics_callbacks()

        # Subscribe to stage lifecycle events to clean up World singleton on any new stage
        # (Ctrl+N, File > New, or programmatic new_stage calls)
        self._subscribe_stage_events()

    def _rebuild_ui(self):
        """Tear down and recreate the panel UI from the current (hot-reloaded) code.
        Use after editing extension.py to pick up new widgets without closing the window.
        Loses transient UI state (collapsed/expanded frames, unsaved field text)."""
        try:
            if not getattr(self, "_window", None):
                print("No window to rebuild")
                return
            self._window.frame.clear()
            with self._window.frame:
                with ui.VStack(spacing=5):
                    self.create_ui()
            print("UI rebuilt from current code")
        except Exception as ex:
            import traceback
            print(f"Error rebuilding UI: {ex}\n{traceback.format_exc()}")

    def _build_visibility_ui(self):
        """Build (or rebuild) the combo boxes for exclude/include.

        Always reads current scene objects and excluded list so the UI
        is up-to-date on first open and after every action.
        """
        scene_objects = self._get_scene_object_names()
        excluded_names = list(self._hidden_objects.keys())
        with ui.VStack(spacing=5, height=0):
            with ui.HStack(spacing=5):
                ui.Label("Object:", alignment=ui.Alignment.LEFT, width=80)
                if scene_objects:
                    self._vis_object_combo = ui.ComboBox(0, *scene_objects, width=150)
                else:
                    self._vis_object_combo = ui.ComboBox(0, "(none)", width=150)
                ui.Button("Exclude", width=80, height=30, clicked_fn=self._on_hide_clicked)
            with ui.HStack(spacing=5):
                ui.Label("Excluded:", alignment=ui.Alignment.LEFT, width=80)
                if excluded_names:
                    self._vis_hidden_combo = ui.ComboBox(0, *excluded_names, width=150)
                else:
                    self._vis_hidden_combo = ui.ComboBox(0, "(none)", width=150)
                ui.Button("Include", width=80, height=30, clicked_fn=self._on_unhide_clicked)

    def _on_hide_clicked(self):
        scene_objects = self._get_scene_object_names()
        if not scene_objects:
            print("No objects to exclude")
            return
        idx = self._vis_object_combo.model.get_item_value_model().as_int
        if idx < len(scene_objects):
            self.hide_object(scene_objects[idx])
        self._visibility_frame.rebuild()

    def _on_unhide_clicked(self):
        hidden_names = list(self._hidden_objects.keys())
        if not hidden_names:
            print("No excluded objects to include")
            return
        idx = self._vis_hidden_combo.model.get_item_value_model().as_int
        if idx < len(hidden_names):
            self.unhide_object(hidden_names[idx])
        self._visibility_frame.rebuild()
