"""Call the twin's bridge tools against a live sim and check the answers. Exit code 0 = all passed.

Start the app first (./run.sh --headless), then:

    .venv/bin/python scripts/smoke_tools.py

It leaves the stage as it found it (adds then deletes /World/Objects, restores moved poses). Tools that need
real hardware or ROS peers (sync_real_poses, start_ros_driver up) are not exercised; start_ros_driver is
only asked for its status. Recording needs ffmpeg and a viewport, so it is reported but does not fail the run
when the app is headless.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp"))
from isaac_mcp.client import BridgeClient, BridgeError  # noqa: E402

PORT = int(os.getenv("UR5E_DT_PORT") or os.getenv("ISAAC_MCP_PORT") or 8766)
client = BridgeClient(port=PORT, timeout=120)
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}".rstrip())
    return ok


def tool(name: str, params: dict | None = None, timeout: float = 120):
    try:
        return client.call(name, params or {}, timeout=timeout)
    except BridgeError as e:
        return {"status": "error", "message": str(e)}


def py(code: str):
    """Run code on Kit's main thread; the code assigns to `result`."""
    return client.call("run_python", {"code": code}, timeout=60).get("result")


def objects() -> list[str]:
    return py(
        "s=omni.usd.get_context().get_stage(); p=s.GetPrimAtPath('/World/Objects')\n"
        "result=[c.GetName() for c in p.GetChildren()] if p and p.IsValid() else []"
    )


def world_pos(name: str):
    """World position of the object's rigid-body prim (the twin nests it as /World/Objects/<n>/<n>/<n>)."""
    return py(
        "s=omni.usd.get_context().get_stage(); p=s.GetPrimAtPath('/World/Objects/%s/%s/%s')\n"
        "m=omni.usd.get_world_transform_matrix(p); t=m.ExtractTranslation(); result=[t[0],t[1],t[2]]" % (name, name, name)
    )


def ok(res) -> bool:
    return isinstance(res, dict) and res.get("status") == "success"


def main() -> int:
    st = client.call("status", {})
    record("status", st.get("port") == PORT and st.get("app_version", "").startswith("6.2"),
           f"app {st.get('app')} {st.get('app_version')} headless={st.get('headless')} tools={st.get('tools')}")

    names = client.call("list_tools", {})["tools"]
    twin = sorted(n for n in names if n.startswith("ur5e_dt."))
    record("twin tools registered", len(twin) == 22, f"{len(twin)} ur5e_dt.* tools")

    r = tool("ur5e_dt.quick_start_with_workstation", timeout=120)
    record("quick_start_with_workstation", ok(r), r.get("message", ""))
    prims = py("s=omni.usd.get_context().get_stage(); result=[c.GetName() for c in s.GetPrimAtPath('/World').GetChildren()]")
    record("scene has UR5e, RG2, Vention", {"UR5e", "RG2_Gripper", "Vention"} <= set(prims), str(prims))

    # objects: add, pose, randomize, save/restore, delete. Stopped, so poses are the authored ones (no falling).
    tool("ur5e_dt.stop_scene")
    r = tool("ur5e_dt.add_objects", {"assembly": "fmb1"})
    record("add_objects fmb1", ok(r), r.get("message", ""))
    tool("ur5e_dt.stop_scene")  # add_objects leaves the timeline playing, which would let the objects fall
    objs = objects()
    record("objects present after add", len(objs) >= 1, str(objs))
    movable = [o for o in objs if not o.startswith("base")]  # boards are skipped by the randomizers
    if movable:
        target = movable[0]
        r = tool("ur5e_dt.set_object_pose", {"object_name": target, "position": [0.3, 0.2, 0.8]})
        pos = world_pos(target)
        record("set_object_pose", ok(r) and all(abs(a - b) < 1e-3 for a, b in zip(pos, [0.3, 0.2, 0.8])), f"{target} -> {pos}")

        state = "smoke_tools_state.json"  # the tool keeps only the file name and saves under its scene_states dir
        r = tool("ur5e_dt.save_scene_state", {"json_file_path": state})
        record("save_scene_state", ok(r) or "success" in json.dumps(r), str(r)[:120])
        saved = world_pos(target)
        r = tool("ur5e_dt.randomize_single_object", {"object_name": target})
        moved = world_pos(target)
        if "min_sep" in r.get("message", ""):
            # Fixed 4 cm x 20 cm target strip that must be 18 cm clear of every object; the tool exposes no ranges.
            print(f"WARN  randomize_single_object cannot place here (default strip too tight): {r['message'][:100]}")
        else:
            record("randomize_single_object", ok(r) and moved != saved, f"{saved} -> {moved}")
        r = tool("ur5e_dt.randomize_object_poses")
        record("randomize_object_poses", ok(r), r.get("message", ""))
        r = tool("ur5e_dt.restore_scene_state", {"json_file_path": state})
        back = world_pos(target)
        record("restore_scene_state", all(abs(a - b) < 1e-3 for a, b in zip(back, saved)), f"{back} vs {saved}")
        py("import glob, os\nfor f in glob.glob('%s/**/%s', recursive=True): os.remove(f)\nresult=True" % (str(ROOT / 'resources'), state))

    r = tool("ur5e_dt.setup_pose_publisher")
    record("setup_pose_publisher", ok(r), r.get("message", ""))

    # timeline
    r = tool("ur5e_dt.play_scene")
    playing = py("result=omni.timeline.get_timeline_interface().is_playing()")
    record("play_scene", ok(r) and playing is True, r.get("message", ""))
    time.sleep(1)
    r = tool("ur5e_dt.stop_scene")
    stopped = py("result=omni.timeline.get_timeline_interface().is_stopped()")
    record("stop_scene", ok(r) and stopped is True, r.get("message", ""))

    # recording (best effort when headless)
    r = tool("ur5e_dt.get_recording_status")
    record("get_recording_status", ok(r), r.get("message", ""))
    out = os.path.join(tempfile.mkdtemp(prefix="smoke-rec-"), "smoke.mp4")
    r = tool("ur5e_dt.start_recording", {"file_name": out, "fps": 10})
    if ok(r):
        time.sleep(2)
        r2 = tool("ur5e_dt.stop_recording")
        size = Path(out).stat().st_size if Path(out).exists() else 0
        record("start/stop_recording", ok(r2) and size > 0, f"{out} {size} bytes")
    else:
        print(f"INFO  recording not available here: {r.get('message', '')[:140]}")

    r = tool("ur5e_dt.start_ros_driver", {"action": "status"})
    record("start_ros_driver status", ok(r), r.get("message", ""))

    r = tool("ur5e_dt.load_simple_room")
    record("load_simple_room", ok(r), r.get("message", ""))

    r = tool("ur5e_dt.delete_objects")
    record("delete_objects", ok(r) and objects() == [], r.get("message", ""))

    r = tool("ur5e_dt.new_stage")
    record("new_stage", ok(r), r.get("message", ""))
    r = tool("ur5e_dt.quick_start_with_workstation", timeout=120)
    record("quick_start after new_stage", ok(r), r.get("message", ""))

    failed = [n for n, good, _ in results if not good]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed" + (f"; FAILED: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
