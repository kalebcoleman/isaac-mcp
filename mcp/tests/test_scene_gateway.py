import asyncio
import json
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from isaac_mcp import server as gw
from isaac_mcp.scene_contracts import REGISTRY
from isaac_mcp.host_catalog import load_catalog


def test_full_catalog_and_errors(monkeypatch):
    monkeypatch.delenv("ISAAC_MCP_NAMESPACE", raising=False)
    catalog = load_catalog()
    builtins = {"run_python", "status", "list_bridges", "reload_tools", "put_file", "get_file", "stat_file"}
    assert len(catalog) == 23 + len(REGISTRY) + len(builtins)
    assert {k for k in catalog if "." not in k} == builtins
    assert len([k for k in catalog if k.startswith("ur5e_dt.")]) == 23
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})

    class Offline:
        def call(self, *args, **kw):
            raise ConnectionRefusedError("test offline")

        def close(self):
            pass

    gw._state_for(server).client = Offline()

    async def run():
        async with create_connected_server_and_client_session(server._mcp_server) as c:
            await c.initialize()
            listed = {t.name: t for t in (await c.list_tools()).tools}
            assert all(gw._mcp_name(k) in listed for k in REGISTRY)
            schema = listed["assets__import_usd"].inputSchema
            assert {
                "session_id",
                "stage_id",
                "root_id",
                "relative_path",
                "prim_path",
            } <= set(schema["required"])
            assert "snapshot_id" not in schema["properties"]
            assert schema["properties"]["orientation"]["items"]["type"] == "number"
            assert schema["properties"]["orientation"]["maxItems"] == 4
            assert "quaternion" in schema["properties"]["orientation"]["description"]
            assert not listed["assets__import_usd"].annotations.readOnlyHint
            for name in ("assets__import_path", "assets__prepare_workstation"):
                schema = listed[name].inputSchema
                assert {"session_id", "stage_id", "source_path"} <= set(
                    schema["required"]
                )
                assert not listed[name].annotations.readOnlyHint
            assert "prim_path" in listed["assets__import_path"].inputSchema["required"]
            save_schema = listed["sim__stage__save"].inputSchema
            reset = save_schema["properties"]["reset_camera_render_handles"]
            assert reset["type"] == "boolean" and reset["default"] is False
            assert "reset_camera_render_handles" not in save_schema["required"]
            assert "export policy" in reset["description"]
            camera_wait = listed["sim__camera__capture"].inputSchema["properties"][
                "wait_for_render"
            ]
            lidar_wait = listed["sim__lidar__read"].inputSchema["properties"][
                "wait_for_render"
            ]
            assert camera_wait["default"] is True and lidar_wait["default"] is False
            assert (
                "NOT_READY" in camera_wait["description"]
                and "tool acquisition" in camera_wait["description"]
            )
            assert "completed scan" in lidar_wait["description"]
            # Student run 2: clear was typed list[float], so no [x, y, r] circle got through.
            clear = listed["sim__map__occupancy"].inputSchema["properties"]["clear"]
            assert clear["maxItems"] == 16
            assert clear["items"]["type"] == "array" and clear["items"]["items"]["type"] == "number"
            assert clear["items"]["minItems"] == clear["items"]["maxItems"] == 3
            roots = await c.call_tool("assets__roots", {})
            assert (
                not roots.isError and json.loads(roots.content[0].text)["roots"] == []
            )
            scan = await c.call_tool("assets__scan", {"root_id": "not-approved"})
            assert scan.isError and "ROOT_NOT_APPROVED" in scan.content[0].text
            info = await c.call_tool("sim__stage__info", {})
            assert info.isError and "not available" in info.content[0].text

    asyncio.run(run())


def test_brokers_are_per_server(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    config = tmp_path / "cfg"
    config.write_text(
        json.dumps(
            dict(
                version=1,
                profile="trusted_project",
                roots={"one": str(root)},
                staging=str(staging),
            )
        )
    )
    a = gw.build_server({"ISAAC_MCP_ASSET_CONFIG": str(config)})
    b = gw.build_server({})
    assert list(gw._state_for(a).asset_broker.roots) == ["one"]
    assert not gw._state_for(b).asset_broker.roots


def test_generic_filters(monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_NAMESPACE", "sim")
    server = gw.build_server({})
    names = server._tool_manager._tools
    assert "sim__stage__info" in names and "assets__roots" not in names
    assert not any(x.startswith("ur5e_dt__") for x in names)


def test_unavailable_profile_does_not_start_trusted_server(tmp_path):
    cfg = tmp_path / "cfg.json"
    cfg.write_text('{"version":1,"profile":"isolated_untrusted"}')
    with pytest.raises(RuntimeError, match="No restricted endpoint"):
        gw.build_server({"ISAAC_MCP_ASSET_CONFIG": str(cfg)})


def test_scene_opt_out_is_per_server_offline_and_after_live_refresh(monkeypatch):
    from isaac_mcp.client import BridgeClient
    from test_bridge import LiveBridge, td

    monkeypatch.delenv("ISAAC_MCP_NAMESPACE", raising=False)
    monkeypatch.delenv("ISAAC_MCP_BUILTINS", raising=False)
    hidden = gw.build_server({"ISAAC_MCP_SCENE_TOOLS": "0"})
    visible = gw.build_server({})
    # The 7 bridge built-ins are listed offline too (PR D), so offline == with a bridge.
    assert len(hidden._tool_manager._tools) == 54
    assert len(visible._tool_manager._tools) == 109
    assert "run_python" in visible._tool_manager._tools and "status" in hidden._tool_manager._tools
    assert "sim__stage__info" in visible._tool_manager._tools
    assert "sim__stage__info" not in hidden._tool_manager._tools
    assert sum(n.startswith("ur5e_dt__") for n in hidden._tool_manager._tools) == 23
    with LiveBridge() as bridge:
        owner = bridge.bridge.register_owner("generic-sim", "sim")
        owner.add(td("stage.info", lambda: {"selected": True}))
        gw._state_for(hidden).client = BridgeClient(port=bridge.port)
        gw._state_for(visible).client = BridgeClient(port=bridge.port)

        async def run():
            for server, enabled, count in ((hidden, False, 54), (visible, True, 109)):
                async with create_connected_server_and_client_session(
                    server._mcp_server
                ) as client:
                    await client.initialize()
                    await client.call_tool("refresh_tools", {})
                    names = {t.name for t in (await client.list_tools()).tools}
                    assert len(names) == count
                    assert ("sim__stage__info" in names) == enabled
                    assert ("assets__roots" in names) == enabled
                    assert "run_python" in names and "ur5e_dt__play_scene" in names
                    if not enabled:
                        refused = await client.call_tool("sim__stage__info", {})
                        assert refused.isError

        asyncio.run(run())


def test_scene_opt_out_rejects_typo_and_keeps_managed_sessions():
    with pytest.raises(ValueError, match="ISAAC_MCP_SCENE_TOOLS"):
        gw.build_server({"ISAAC_MCP_SCENE_TOOLS": "of"})
    server = gw.build_server(
        {"ISAAC_MCP_SCENE_TOOLS": "false", "ISAAC_MCP_LOCAL_TOOLS": "sim_launch"}
    )
    assert "sim__launch" in server._tool_manager._tools
    assert "sim__stage__info" not in server._tool_manager._tools


def test_asset_stale_target_includes_current_identity_before_snapshot(monkeypatch):
    server = gw.build_server({})
    state = gw._state_for(server)
    info = dict(
        session_id="current-session",
        stage_id="current-stage",
        pid=123,
        source="anon:current-stage",
        default_prim="/World",
        meters_per_unit=1,
        up_axis="Z",
    )

    class Client:
        def call(self, name):
            assert name == "sim.stage.info"
            return info

    state.client = Client()
    monkeypatch.setattr(
        state.asset_broker,
        "snapshot",
        lambda *a, **kw: pytest.fail("stale call copied source files"),
    )
    out = json.loads(
        gw._dispatch("assets.import_usd", dict(session_id="old", stage_id="old"), state)
    )
    assert out["code"] == "STALE_TARGET"
    assert out["current_target"] == info


@pytest.mark.parametrize("tool", ["assets.import_path", "assets.prepare_workstation"])
def test_absolute_path_is_snapshotted_before_bridge_dispatch(tmp_path, tool):
    root = tmp_path / "root"
    root.mkdir()
    source = root / "asset.usda"
    source.write_text("#usda 1.0\n")
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            dict(
                version=1,
                profile="trusted_project",
                roots={"project": str(root)},
                staging=str(staging),
            )
        )
    )
    server = gw.build_server({"ISAAC_MCP_ASSET_CONFIG": str(config)})
    state = gw._state_for(server)
    seen = []

    class Client:
        def call(self, name, params=None):
            if name == "sim.stage.info":
                return dict(
                    session_id="session", stage_id="stage", staging=str(staging)
                )
            seen.append((name, params))
            filename, _ = state.asset_broker.verify(
                params["snapshot_id"], params["manifest_sha256"]
            )
            assert filename.read_text() == source.read_text()
            assert "source_path" not in params
            return dict(status="ok", prepared=True)

    state.client = Client()
    params = dict(session_id="session", stage_id="stage", source_path=str(source))
    if tool == "assets.import_path":
        params["prim_path"] = "/World/Imported"
    out = json.loads(gw._dispatch(tool, params, state))
    assert out["status"] == "ok" and seen[0][0] == tool
    denied = json.loads(
        gw._dispatch(
            tool, {**params, "source_path": str(tmp_path / "other.usda")}, state
        )
    )
    assert denied["code"] == "ROOT_NOT_APPROVED" and len(seen) == 1


def _urdf_state(tmp_path):
    root = tmp_path / "root"
    (root / "urdf").mkdir(parents=True)
    source = root / "urdf" / "r.urdf"
    source.write_text('<robot name="r"><link name="a"/></robot>')
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    config = tmp_path / "config.json"
    config.write_text(json.dumps(dict(version=1, profile="trusted_project", roots={"project": str(root)}, staging=str(staging))))
    state = gw._state_for(gw.build_server({"ISAAC_MCP_ASSET_CONFIG": str(config)}))
    seen = []

    class Client:
        def call(self, name, params=None):
            if name == "sim.stage.info":
                return dict(session_id="session", stage_id="stage", staging=str(staging))
            filename, manifest = state.asset_broker.verify(params["snapshot_id"], params["manifest_sha256"], kind="urdf")
            seen.append((name, params, manifest))
            assert filename.read_text() == source.read_text() and "source_path" not in params
            return dict(status="ok")

    state.client = Client()
    return state, source, seen


def test_urdf_by_source_path_or_root_is_snapshotted_before_dispatch(tmp_path):
    state, source, seen = _urdf_state(tmp_path)
    ids = dict(session_id="session", stage_id="stage", prim_path="/World/R")
    assert json.loads(gw._dispatch("assets.import_urdf", {**ids, "source_path": str(source)}, state))["status"] == "ok"
    assert json.loads(gw._dispatch("assets.import_urdf", {**ids, "root_id": "project", "relative_path": "urdf/r.urdf"}, state))["status"] == "ok"
    assert [item[2]["entry"] for item in seen] == ["urdf/r.urdf", "urdf/r.urdf"]
    assert seen[0][2]["root_name"] == "root"


@pytest.mark.parametrize("extra,code", [
    (dict(), "INVALID_VALUE"),
    (dict(root_id="project"), "INVALID_VALUE"),
    (dict(source_path="/x/y.urdf", root_id="project", relative_path="urdf/r.urdf"), "INVALID_VALUE"),
    (dict(source_path="/not/approved.urdf"), "ROOT_NOT_APPROVED"),
])
def test_urdf_source_must_be_one_approved_thing(tmp_path, extra, code):
    state, _, seen = _urdf_state(tmp_path)
    out = json.loads(gw._dispatch("assets.import_urdf", dict(session_id="session", stage_id="stage", prim_path="/World/R", **extra), state))
    assert out["code"] == code and not seen


def test_quick_start_sends_a_pack_urdf_through_the_host(tmp_path, monkeypatch):
    store = tmp_path / "store"
    (store / "bot").mkdir(parents=True)
    (store / "bot" / "r.urdf").write_text('<robot name="r"><link name="a"/></robot>')
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(store))
    monkeypatch.setenv("ISAAC_MCP_PROJECT", str(tmp_path / "none"))
    state, _, seen = _urdf_state(tmp_path)
    assert json.loads(gw._dispatch("sim.quick_start", dict(robot="bot/r.urdf"), state))["status"] == "ok"
    assert seen[0][0] == "sim.quick_start" and seen[0][1]["robot"] == "bot/r.urdf" and seen[0][2]["root_name"] == "bot"
    out = json.loads(gw._dispatch("sim.quick_start", dict(robot="bot/none.urdf"), state))
    assert out["code"] == "ASSET_MISSING" and len(seen) == 1
    out = json.loads(gw._dispatch("sim.quick_start", dict(robot="bad..name/r.urdf"), state))
    assert out["code"] in ("INVALID_VALUE", "ASSET_MISSING") and len(seen) == 1


def test_quick_start_refuses_a_pack_xacro_with_the_guidance(tmp_path, monkeypatch):
    store = tmp_path / "store"
    (store / "bot").mkdir(parents=True)
    (store / "bot" / "r.urdf.xacro").write_text("<robot/>")
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(store))
    monkeypatch.setenv("ISAAC_MCP_PROJECT", str(tmp_path / "none"))
    state, _, seen = _urdf_state(tmp_path)
    out = json.loads(gw._dispatch("sim.quick_start", dict(robot="bot/r.urdf.xacro"), state))
    assert out["code"] == "XACRO" and not seen


def test_a_newer_bridge_parameter_does_not_break_discovery():
    # The two checkouts sync separately: a bridge that already serves sim.ui.act's `window` parameter
    # met an older host registry, and discovery failed for every tool ("invalid bridge tool").
    fields = [dict(name="action", kind="string", enum=None, required=True, default=None, has_default=False,
                   description="What to do."),
              dict(name="brand_new", kind="string", enum=None, required=False, default=None, has_default=False,
                   description="A parameter this host does not know yet.")]
    fields.append(dict(name="titles", kind="array", enum=None, required=False, default=None, has_default=False,
                       description="An unknown list of strings."))
    fields.append(dict(name="future", kind="hyperthing", enum=None, required=False, default=None,
                       has_default=False, description="An unknown kind."))
    fn = gw._build_tool_function("sim__ui__act", "sim.ui.act", "d", fields)
    assert {"brand_new", "titles", "future"} <= set(fn.__annotations__)
    import pydantic

    # Unknown arrays keep the bridge's plain list type: strings are not rejected as non-numbers.
    pydantic.TypeAdapter(fn.__annotations__["titles"]).validate_python(["Console"])
    pydantic.TypeAdapter(fn.__annotations__["brand_new"]).validate_python("x")
