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
    assert len(catalog) == 23 + len(REGISTRY)
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
    assert len(hidden._tool_manager._tools) == 45
    assert len(visible._tool_manager._tools) == 85
    assert "sim__stage__info" in visible._tool_manager._tools
    assert "sim__stage__info" not in hidden._tool_manager._tools
    assert sum(n.startswith("ur5e_dt__") for n in hidden._tool_manager._tools) == 23
    with LiveBridge() as bridge:
        owner = bridge.bridge.register_owner("generic-sim", "sim")
        owner.add(td("stage.info", lambda: {"selected": True}))
        gw._state_for(hidden).client = BridgeClient(port=bridge.port)
        gw._state_for(visible).client = BridgeClient(port=bridge.port)

        async def run():
            for server, enabled, count in ((hidden, False, 52), (visible, True, 92)):
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
