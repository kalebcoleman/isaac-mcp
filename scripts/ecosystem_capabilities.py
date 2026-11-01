"""Capability areas for docs/ECOSYSTEM.md: how each external tool is classified, and what
isaac-mcp already offers in that area. Used by scripts/ecosystem_catalog.py.

Classification is rule-based: a repository-wide or per-tool override first, then the first
name pattern in AREA_RULES that matches. Every rule is listed here so a misfiled tool can be
fixed by editing one line. Verdicts (status, plan, priority) are written by hand from the
tool references in docs/ and the sim.* design in docs/GENERIC-SIM-TOOLS.md.
"""

import re

# Repositories whose tools are already part of isaac-mcp (vendored upstreams). They are left
# out of the capability tables and counted separately.
UPSTREAM = {"inbarajaldrin/omni-kit-mcp", "inbarajaldrin/isaac-sim-mcp"}

# key: (title, software, isaac-mcp today, status label, plan, priority, why)
AREAS = {
    "connect": (
        "Connection, status and capabilities", "Isaac Sim / Kit",
        "`refresh_tools`, `use_bridge`, `status`, `list_bridges`, the `--list-bridges` CLI.",
        "Have", "—", "Done",
        "Every server has a ping; isaac-mcp's bridge discovery and PID/port check go further."),
    "lifecycle": (
        "Start and stop the simulator", "Isaac Sim / Kit",
        "`sim__launch`, `sim__list`, `sim__status`, `sim__stop` (opt-in `sim_launch` group), `./run.sh`.",
        "Have", "—", "Done",
        "dex-isaac-mcp's `sim_reload` (restart with a different robot) is the only idea not covered."),
    "code": (
        "Python, Kit commands and extensions", "Isaac Sim / Kit",
        "`run_python` (persistent sessions), `reload_tools`.",
        "Have", "—", "Low",
        "Missing extras: run a script file by path, toggle Kit extensions (`manage_extensions`), "
        "a script audit log. All are a few lines on top of `run_python` and `put_file`."),
    "files": (
        "Files, artifacts and background jobs", "Isaac Sim / Kit",
        "`put_file`, `get_file`, `stat_file`; Isaac Lab jobs via `isaaclab__runs_*`.",
        "Have", "—", "Low",
        "Tim0320/IsaacSim-MCP's managed artifacts (hash, expiry, chunked reads) matter only "
        "for large outputs over a remote link."),
    "stage": (
        "Stage files, layers and variants", "USD",
        "`ur5e_dt__new_stage`, `ur5e_dt__save_scene_state` / `restore_scene_state` (twin "
        "poses only); anything else through `run_python`.",
        "Partial", "`stage.save` (phase 3)", "Medium",
        "Open, save-as, sublayers, references, variant selection and flatten/export are common "
        "and easy to get subtly wrong by hand (anonymous stages, edit targets)."),
    "inspect": (
        "Scene inspection and measurement", "USD",
        "`run_python` only.",
        "run_python only", "`stage.info`, `stage.tree`, `prim.get`, `prim.find` (phase 1)",
        "High",
        "Present in most scene-editing servers and the first thing an agent needs. Bounds, "
        "distance and mesh statistics (`get_prim_bounds`, `measure_distance`, `get_mesh_stats`) "
        "are not yet in the design."),
    "edit": (
        "Create, edit and arrange prims", "USD",
        "`run_python`; the twin's `ur5e_dt__add_objects`, `delete_objects`, `set_object_pose` "
        "for its own objects.",
        "run_python only",
        "`prim.create`, `set_transform`, `set_attribute`, `duplicate`, `delete` (phase 2)",
        "High",
        "Typed attribute setting with read-back (Tim0320) and dry-run previews are worth "
        "copying; layout helpers (grid, scatter, align) are optional."),
    "assets": (
        "Assets, robots and environments", "Isaac Sim / USD",
        "Twin loaders only (`ur5e_dt__quick_start*`, `load_workstation`, `load_simple_room`).",
        "Partial", "not in the design", "High",
        "A generic `load_usd` / `import_urdf` / robot library is what makes the server "
        "robot-agnostic. whats2000's robot library and serionist's `create_robot` are good "
        "references."),
    "look": (
        "Materials, lights and rendering", "USD / RTX",
        "`run_python` only.",
        "run_python only", "not in the design", "Low",
        "Useful for demos and synthetic data, not for control or training."),
    "physics": (
        "Physics setup and queries", "PhysX",
        "`run_python` only.",
        "run_python only", "`physics.scene_info`, `physics.apply`, `raycast` (phases 1-2)",
        "Medium",
        "Physics scene, rigid body, collider and joint creation recur everywhere; `apply_force` "
        "and overlap tests are not in the design."),
    "simctl": (
        "Simulation playback and stepping", "Isaac Sim / Kit",
        "`ur5e_dt__play_scene`, `ur5e_dt__stop_scene` (twin only).",
        "Partial", "`sim.state`, `play`, `pause`, `stop`, `step` (phases 1, 3)", "High",
        "Exact N-step stepping with a state read is how most servers make experiments "
        "reproducible."),
    "joints": (
        "Robot state and joint control", "Isaac Sim",
        "None. The twin pack registers no joint tools; motion goes through a ROS driver.",
        "Missing", "`articulation.state` (read only, phase 1)", "High",
        "The largest gap: most Isaac Sim servers can read joints and set targets or drive gains "
        "on any articulation. GenRobo/isaac-sim-mcp has the clearest named-joint API."),
    "tuning": (
        "Robot tuning experiments", "Isaac Sim",
        "None.",
        "Missing", "not in the design", "Medium",
        "dex-isaac-mcp only: range tests, tracking error, parameter sweeps that restore the "
        "original value. Cheap to add once joint control exists, and gives agents numbers."),
    "motion": (
        "Grippers, IK, trajectories and skills", "Isaac Sim",
        "None in Kit; `ur5e_dt__start_ros_driver` for ROS-based motion.",
        "Missing", "not in the design", "Medium",
        "Tim0320/IsaacSim-MCP has bounded Lula IK, trajectory jobs, grippers and mobile bases; "
        "worth studying if agents should move robots without ROS."),
    "sensors": (
        "Cameras and sensors", "Isaac Sim",
        "`run_python`; `ur5e_dt__setup_camera_tf_graph` for the twin camera.",
        "run_python only", "`camera.list` (phase 1)", "Medium",
        "Camera/LiDAR creation, annotator capture (depth, segmentation) and calibration reads."),
    "capture": (
        "Viewport capture and recording", "Isaac Sim / desktop",
        "`screenshot`, `start_screen_recording` / `stop_screen_recording`, "
        "`ur5e_dt__start_recording`.",
        "Have", "`capture.screenshot`, `record_start` / `record_stop` (phases 1, 3)", "Medium",
        "Headless capture from a chosen camera, auto-framing a robot and multi-angle prim "
        "views (serionist `inspect_prim`) would make results visible without a desktop."),
    "ros": (
        "ROS 2 and OmniGraph", "Isaac Sim / ROS 2",
        "Twin only: `ur5e_dt__setup_camera_tf_graph`, `setup_pose_publisher`, "
        "`start_ros_driver`, `sync_real_poses`.",
        "Partial", "not in the design", "Medium",
        "Generic Clock / TF / JointState / camera publishers and Action Graph editing "
        "(Tim0320/IsaacSim-MCP) would cover other robots."),
    "sdg": (
        "Randomization and synthetic data", "Replicator",
        "`ur5e_dt__randomize_object_poses`, `randomize_single_object` (twin only).",
        "Partial", "not in the design", "Low",
        "Semantic labels and Replicator jobs matter for perception datasets, not for control."),
    "humans": (
        "Humans, animation and navigation", "Isaac Sim / Omniverse",
        "None.",
        "Missing", "not in the design", "Low",
        "Animated people, crowds, keyframes and navmesh baking; outside the project's focus."),
    "logs": (
        "Logs and diagnostics", "Isaac Sim / Kit",
        "None (the agent reads log files through the shell or `get_file`).",
        "Missing", "not in the design", "High",
        "A bridge tool returning recent Kit WARN/ERROR lines is cheap and shortens every "
        "debugging loop; five servers have one."),
    "lab": (
        "Isaac Lab training and evaluation", "Isaac Lab",
        "The 12 `isaaclab__*` tools: install info, task list, train/eval/play jobs, status, "
        "tail, cancel, checkpoints, TensorBoard metrics.",
        "Have", "—", "Medium",
        "Missing: interactive env sessions (create, step, observe, reset) from "
        "chloepilonv/mcp-server-isaaclab, video listing, and a GPU status read."),
    "design": (
        "Task design, scene specs and evidence", "Isaac Lab / Isaac Sim",
        "None.",
        "Missing", "not in the design", "Low",
        "Contracts and evidence bundles (Tim0320/IsaacLab-MCP) and simscene's verified scene "
        "specs are research-grade ideas for reproducibility, not core tools."),
    "docs": (
        "Documentation and API search", "Kit / USD / Isaac Sim",
        "None.",
        "Missing", "not needed", "Skip",
        "Run NVIDIA's own servers (kit-usd-agents) next to isaac-mcp instead of rebuilding them."),
    "agentic": (
        "LLM-inside and routing tools", "—",
        "None.",
        "Missing", "not needed", "Skip",
        "Tools that call an LLM themselves duplicate what the client agent already does."),
    "skills": (
        "Task-specific robot skills", "Isaac Sim",
        "Twin-specific tools in `ur5e_dt__*`.",
        "Project-specific", "—", "Skip",
        "Doors, elevators and pick/place for one benchmark; examples of a pack, not reusable."),
}

# Whole repositories that belong to one area.
REPO_AREA = {
    "NVIDIA-Omniverse/kit-usd-agents": "docs",
    "waynehacking8/physgate": "skills",
    "dcmcshan/isaacsim-mcp": "design",
}

# (repository or None for any repository, exact tool name) -> area, for names the patterns
# would misfile.
TOOL_AREA = {
    (None, "execute_trajectory"): "motion",
    (None, "evaluate_action_graph"): "ros",
    (None, "export_prim_as_file"): "stage",
    (None, "get_stage_info"): "inspect",
    (None, "get_stage_hierarchy"): "inspect",
    (None, "set_drive_params"): "joints",
    ("chloepilonv/mcp-server-isaaclab", "list_environments"): "lab",
    ("chloepilonv/mcp-server-isaaclab", "read_remote_file"): "files",
    ("chloepilonv/mcp-server-isaaclab", "gpu_status"): "logs",
    ("chloepilonv/mcp-server-isaaclab", "list_videos"): "lab",
    ("sandraschi/isaac-mcp", "list_jobs"): "lifecycle",
    ("sandraschi/isaac-mcp", "show_jobs_card"): "lifecycle",
    ("sandraschi/isaac-mcp", "show_scenes_card"): "stage",
    ("sandraschi/isaac-mcp", "analyze_sim_logs"): "agentic",
    ("sandraschi/isaac-mcp", "get_state"): "joints",
    ("Gonglitian/isaac-sim-mcp-suite", "get_scene_info"): "connect",
    ("ros-claw/simscene", "job_status"): "files",
    ("ros-claw/simscene", "job_cancel"): "files",
    ("ros-claw/simscene", "simulation_start"): "design",
    ("ros-claw/simscene", "simulation_pause"): "design",
    ("ros-claw/simscene", "simulation_resume"): "design",
    ("ros-claw/simscene", "scene_observe"): "capture",
    ("ros-claw/simscene", "run_video_chunk"): "capture",
}

# First match wins, so specific patterns come before general ones.
AREA_RULES = [
    ("design", r"^(design_|create_lifting|validate_|scene_(open|get|apply|validate|plan_)|"
               r"run_(observe|contacts|verify)|run_presentation|load_humanoid_config|"
               r"run_component_sim|run_monte_carlo|get_monte_carlo)"),
    ("agentic", r"^(agentic_|natural_language_|analyze_sim_state|run_active_inference|"
                r"isaac_pipeline|router_)"),
    ("docs", r"(^learning_|_knowledge$)"),
    ("lab", r"(^train_|training|evaluat|^list_checkpoints$|^list_log_dirs$|isaaclab|isaac_lab|"
            r"^lab_runtime|^(create|step|reset|close)_session$|^get_observation$)"),
    ("connect", r"^(ping|check_isaac_connection|get_scene_info|status|get_capabilities|"
                r"get_runtime_status|sim_status|show_sim_status_card|connect_instance|"
                r"disconnect_instance|instance_status|list_bridges|refresh_tools|list_tools|"
                r"environment_(probe|configure))$"),
    ("lifecycle", r"^(sim_up|sim_down|sim_reload|start_sim|stop_sim|isaac_shutdown)$"),
    ("code", r"(^execute_|^run_python$|omni_kit_command|^reload_script$|^get_script_|"
             r"manage_extensions|^reload_tools$)"),
    ("logs", r"(log|console_errors)"),
    ("sdg", r"(sdg|replicator|semantic|randomiz)"),
    ("humans", r"(human|character|crowd|keyframe|animation|navmesh)"),
    ("ros", r"(action_graph|ros2|ros_|script_node|pose_publisher|sync_real_poses)"),
    ("motion", r"(gripper|mobile_base|_ik$|solve_ik|trajectory|motion|move_to_pose|"
               r"controller_profile|robot_(prepare_profile|suggest_configuration|plan_pick))"),
    ("tuning", r"^sim_(range_test|wave|sweep|set_params|set_coupling|get_stats)$"),
    ("physics", r"(physics|rigid_body|collider|ground_plane|collision_group|^create_joint$|"
                r"force|overlap_test|raycast)"),
    ("joints", r"(joint|get_robot_info|articulation|^list_robots$|apply_control|"
               r"^sim_(set_targets|list_poses|set_pose)$)"),
    ("simctl", r"(_simulation$|simulation_control|^sim_control$|control_timeline|"
               r"simulation_state|^get_sim_state$|simulation_speed|^set_timeline$|"
               r"^(play|stop)_scene$|^sim_(step|reset|play)$)"),
    ("capture", r"(capture_viewport|screenshot|^sim_capture$|frame_robot|capture_camera$|"
                r"backdrop|record|^inspect_prim$|^video_|draw_debug|^set_camera$|"
                r"^sim_set_camera$|set_active_camera|render_to_file)"),
    ("sensors", r"(camera|lidar|capture_image|delete_sensor)"),
    ("files", r"(artifact|job|file$|^read_remote)"),
    ("stage", r"(_stage|_scene$|^new_scene$|^open_scene$|stage_composition|sublayer|"
              r"composition_arc|variant|add_reference|create_layer|flatten_usd|export_prim|"
              r"stage_batch|^load_scene$|^list_scenes$|scene_state|^scene_export$)"),
    ("assets", r"(load_usd|import_urdf|import_asset|search_usd|search_3d|generate_3d|"
               r"nvidia_asset|^list_assets$|^asset_|huggingface|discover_model|convert_urdf|"
               r"robot_library|available_robots|^create_robot$|^spawn_robot$|spawn_model|"
               r"^(list|load)_environment|load_workstation|load_simple_room|quick_start|"
               r"^create_(warehouse|solar_farm|wind_farm|factory_floor|data_center)$)"),
    ("look", r"(light|material|environment|sky|render)"),
    ("inspect", r"(list_prims|prim_info|query_prim|stage_info|stage_hierarchy|scene_tree|"
                r"prim_properties|bounds|bounding_box|measure|find_prims|^scene_(summary|"
                r"inspect|children|find|select)$|mesh_stats|compare_prims|get_typed_attribute|"
                r"^get_transform$|query_scene)"),
    ("edit", r"(create_prim|create_mesh|modify_prim|delete_prim|duplicate|clone|group_prims|"
             r"_object|^transform|set_prim_transform|^set_transform$|^move_prim$|align_prims|"
             r"distribute_prims|create_grid|_layout$|scatter_prims|set_visibility|"
             r"^scene_(create|delete|reparent|move|rename)$|typed_attribute|_objects$|"
             r"^add_cube$|object_pose)"),
]
_COMPILED = [(area, re.compile(pattern)) for area, pattern in AREA_RULES]


def classify(repo, name):
    """Return the area key for one tool, or "other" when no rule applies."""
    for key in ((repo, name), (None, name)):
        if key in TOOL_AREA:
            return TOOL_AREA[key]
    if repo in REPO_AREA:
        return REPO_AREA[repo]
    for area, pattern in _COMPILED:
        if pattern.search(name):
            return area
    return "other"
