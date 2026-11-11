"""MCP hints for catalogued tools that have no versioned contract: bridge built-ins and the twin.

The generic pack's hints are in ``server.py``, next to its contract list. These are advisory, not
enforcement, and a test requires an entry for every catalogued tool. Bridge tools from a project's
own extension are not listed here and carry no hints.
"""

from __future__ import annotations


def _hints(read_only=False, destructive=False, idempotent=False, open_world=False):
    return dict(readOnlyHint=read_only, destructiveHint=destructive, idempotentHint=idempotent,
                openWorldHint=open_world)


_READ = _hints(read_only=True, idempotent=True)
# Changes the scene or writes a new file, and does not remove anything the user made.
_EDIT = _hints()
_SAME_AGAIN = _hints(idempotent=True)
_DESTROYS = _hints(destructive=True)

CATALOG_HINTS = {
    "status": _READ,
    "list_bridges": _READ,
    "get_file": _READ,
    "stat_file": _READ,
    "job_status": _READ,
    "job_cancel": _hints(destructive=True, idempotent=True),  # ends work in progress
    # Arbitrary code in Kit: it can do anything this OS account can.
    "run_python": _hints(destructive=True, open_world=True),
    "put_file": _hints(destructive=True, idempotent=True),  # replaces a file at that path
    "reload_tools": _DESTROYS,  # unregisters and re-imports a live tool package
    "ur5e_dt.get_recording_status": _READ,
    "ur5e_dt.play_scene": _SAME_AGAIN,
    "ur5e_dt.stop_scene": _SAME_AGAIN,
    "ur5e_dt.setup_camera_tf_graph": _EDIT,
    "ur5e_dt.setup_pose_publisher": _EDIT,
    "ur5e_dt.assemble_objects": _EDIT,
    "ur5e_dt.add_objects": _EDIT,
    "ur5e_dt.randomize_object_poses": _EDIT,
    "ur5e_dt.randomize_single_object": _EDIT,
    "ur5e_dt.set_object_pose": _SAME_AGAIN,
    "ur5e_dt.load_workstation": _EDIT,
    "ur5e_dt.load_simple_room": _EDIT,
    "ur5e_dt.start_recording": _EDIT,
    "ur5e_dt.stop_recording": _EDIT,
    "ur5e_dt.restore_scene_state": _SAME_AGAIN,
    "ur5e_dt.save_scene_state": _DESTROYS,  # a named file replaces one of that name
    "ur5e_dt.delete_objects": _DESTROYS,
    "ur5e_dt.new_stage": _DESTROYS,  # clears the whole stage
    "ur5e_dt.quick_start": _DESTROYS,  # replaces the scene
    "ur5e_dt.quick_start_with_workstation": _DESTROYS,
    "ur5e_dt.quick_start_with_workstation_and_simple_room": _DESTROYS,
    # These reach outside the process: ROS, host subprocesses.
    "ur5e_dt.start_ros_driver": _hints(destructive=True, open_world=True),  # also stops a driver
    "ur5e_dt.sync_real_poses": _hints(open_world=True),
}
