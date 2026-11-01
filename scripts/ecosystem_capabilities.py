"""Capability areas for docs/ECOSYSTEM.md: how each external tool is classified, and what
isaac-mcp already offers in that area. Used by scripts/ecosystem_catalog.py.

Classification is rule-based: a repository-wide or per-tool override first, then the first
name pattern in AREA_RULES that matches. Every rule is listed here so a misfiled tool can be
fixed by editing one line. Verdicts (status, plan, priority) are written by hand from the
tool references in docs/ and the follow-up plan docs/plans/SIM-TOOLS-FOLLOWUP.md.
"""

import re

# Repositories whose tools are already part of isaac-mcp (vendored upstreams). They are left
# out of the capability tables and counted separately.
UPSTREAM = {"inbarajaldrin/omni-kit-mcp", "inbarajaldrin/isaac-sim-mcp"}

# key: (title, software, isaac-mcp today, status label, plan, priority, why)
# "isaac-mcp today" counts the sim__*/assets__* pack from PR #11 (exts/sim-tools). "Plan" names a
# milestone in docs/plans/SIM-TOOLS-FOLLOWUP.md.
AREAS = {
    "connect": (
        "Connection, status and capabilities", "Isaac Sim / Kit",
        "`refresh_tools`, `use_bridge`, `status`, `list_bridges`, the `--list-bridges` CLI; "
        "`sim__stage__info` for session and stage identity.",
        "Have", "—", "Done",
        "Every server has a ping; isaac-mcp's bridge discovery and PID/port check go further."),
    "lifecycle": (
        "Start and stop the simulator", "Isaac Sim / Kit",
        "`sim__launch`, `sim__list`, `sim__status`, `sim__stop` (opt-in `sim_launch` group), `./run.sh`.",
        "Have", "—", "Done",
        "dex-isaac-mcp's `sim_reload` (restart with a different robot) is the only idea not covered."),
    "code": (
        "Python, Kit commands and extensions", "Isaac Sim / Kit",
        "`run_python` (persistent sessions), `reload_tools`; opt-in ScriptNode controllers "
        "(`sim__controller__create` / `update`).",
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
        "`sim__stage__save` (flattened export), `sim__stage__clear_owned`; twin "
        "`ur5e_dt__new_stage` and pose save/restore.",
        "Partial", "—", "Medium",
        "Open, sublayers, references and variant selection are common and easy to get subtly "
        "wrong by hand (anonymous stages, edit targets)."),
    "inspect": (
        "Scene inspection and measurement", "USD",
        "`sim__stage__info`, `sim__stage__tree`, `sim__prim__get`, `sim__prim__find`, "
        "`sim__physics__inspect`.",
        "Have", "bounds, distances, camera list (M1)", "Medium",
        "World bounds, distances and mesh statistics (`get_prim_bounds`, `measure_distance`, "
        "`get_mesh_stats`) are the remaining gap."),
    "edit": (
        "Create, edit and arrange prims", "USD",
        "`sim__prim__create`, `set_transform`, `clone`, `delete` on prims the pack created, in "
        "the session layer.",
        "Partial", "any prim, typed attributes, revert (M5)", "High",
        "Moving and adding physics to any prim (imports and the user's own USD) is planned; "
        "layout helpers (grid, scatter, align) are optional."),
    "assets": (
        "Assets, robots and environments", "Isaac Sim / USD",
        "`assets__roots`, `scan`, `search`, `info`, `import_usd`, `import_urdf` for trusted "
        "project folders; twin loaders; UR5e assets fetched on demand.",
        "Partial", "NVIDIA cloud library, bundled samples, MJCF (M6)", "High",
        "A browsable robot library and MJCF import are what remain. whats2000's curated library "
        "and serionist's `create_robot` are good references."),
    "look": (
        "Materials, lights and rendering", "USD / RTX",
        "`sim__light__create` / `modify`, `sim__material__create` / `apply`.",
        "Have", "—", "Low",
        "Useful for demos and synthetic data, not for control or training."),
    "physics": (
        "Physics setup and queries", "PhysX / Newton",
        "`sim__physics__configure`, `sim__physics__inspect`, `sim__body__state`; rigid and "
        "collision flags on `sim__prim__create`.",
        "Partial", "raycast, contacts (M3); physics on any prim (M5)", "Medium",
        "Scene queries (raycast, overlap, contacts) and `apply_force` are the remaining gap."),
    "simctl": (
        "Simulation playback and stepping", "Isaac Sim / Kit",
        "`sim__timeline__control` (play, pause, stop), `sim__simulation__step` (exact paused "
        "steps with observed prims and joints).",
        "Have", "—", "Done",
        "Exact N-step stepping with a state read is how most servers make experiments "
        "reproducible; PR #11 measures elapsed physics time on every step."),
    "joints": (
        "Robot state and joint control", "Isaac Sim",
        "`sim__joints__inspect`, `state`, `command` (position targets in DOF order), `drive` "
        "(authored gains, stopped timeline).",
        "Partial", "names, velocities, clamping, runtime gains, move-until-settled (M4)", "High",
        "Name-based targets and a settle-and-measure move are what agents steer with. "
        "GenRobo/isaac-sim-mcp has the clearest named-joint API."),
    "tuning": (
        "Robot tuning experiments", "Isaac Sim",
        "None.",
        "Missing", "tracking error in `sim.joints.move` (M4)", "Medium",
        "dex-isaac-mcp only: range tests, tracking error, parameter sweeps that restore the "
        "original value. Cheap to add once joint control exists, and gives agents numbers."),
    "motion": (
        "Grippers, IK, trajectories and skills", "Isaac Sim",
        "None in Kit; `ur5e_dt__start_ros_driver` for ROS-based motion.",
        "Missing", "—", "Medium",
        "Tim0320/IsaacSim-MCP has bounded Lula IK, trajectory jobs, grippers and mobile bases; "
        "worth studying if agents should move robots without ROS."),
    "sensors": (
        "Cameras and sensors", "Isaac Sim",
        "`sim__camera__create` / `capture` (RTX RGB to PNG), `sim__lidar__create` / `read`; "
        "twin camera via `ur5e_dt__setup_camera_tf_graph`.",
        "Have", "camera list (M1)", "Low",
        "Depth and segmentation annotators and calibration reads are the remaining extras."),
    "capture": (
        "Viewport capture and recording", "Isaac Sim / desktop",
        "`screenshot`, `start_screen_recording` / `stop_screen_recording`, "
        "`ur5e_dt__start_recording`, `sim__viewport__frame`.",
        "Have", "Kit viewport screenshot and MP4 (M7)", "Medium",
        "Headless viewport recording from the pack and multi-angle prim views (serionist "
        "`inspect_prim`) would make results visible without a desktop."),
    "ros": (
        "ROS 2 and OmniGraph", "Isaac Sim / ROS 2",
        "Twin only: `ur5e_dt__setup_camera_tf_graph`, `setup_pose_publisher`, "
        "`start_ros_driver`, `sync_real_poses`.",
        "Partial", "—", "Medium",
        "Generic Clock / TF / JointState / camera publishers and Action Graph editing "
        "(Tim0320/IsaacSim-MCP) would cover other robots."),
    "sdg": (
        "Randomization and synthetic data", "Replicator",
        "`ur5e_dt__randomize_object_poses`, `randomize_single_object` (twin only).",
        "Partial", "—", "Low",
        "Semantic labels and Replicator jobs matter for perception datasets, not for control."),
    "humans": (
        "Humans, animation and navigation", "Isaac Sim / Omniverse",
        "None.",
        "Missing", "—", "Low",
        "Animated people, crowds, keyframes and navmesh baking; outside the project's focus."),
    "logs": (
        "Logs and diagnostics", "Isaac Sim / Kit",
        "`sim__logs__tail` (bounded tail of Kit's log file).",
        "Partial", "filters, cursor, summary (M2)", "Medium",
        "A cursor and a since-now summary turn the tail into a debugging loop; never hook Kit's "
        "logging from Python (two servers deadlocked Kit that way)."),
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
        "Missing", "—", "Low",
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
