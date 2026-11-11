"""Real MCP sessions must not report a lost bridge as successful execution."""

import asyncio
import base64
import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from isaac_mcp import server as gw
from isaac_mcp.client import BridgeError


@pytest.fixture(autouse=True)
def single_purpose_tool_list(monkeypatch):
    """These tests are about the single-purpose tools themselves, so they list them as before the groups."""
    monkeypatch.setenv("ISAAC_MCP_GROUP_TOOLS", "0")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for name in ("ISAAC_MCP_NAMESPACE", "ISAAC_MCP_BUILTINS", "ISAAC_MCP_PORT",
                 "UR5E_DT_PORT", "ISAAC_MCP_HOST"):
        monkeypatch.delenv(name, raising=False)


def invoke(*, value=None, error=None):
    class Endpoint:
        port = 12345

        def call(self, name, params=None, **kwargs):
            if name == "list_tools":
                raise ConnectionRefusedError("fixture discovery is offline")
            if error is not None:
                raise error
            return value

        def close(self):
            pass

    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "none"})
    state = gw._state_for(server)
    state.client = Endpoint()

    async def run():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            await client.initialize()
            before = next(t for t in (await client.list_tools()).tools
                          if t.name == "ur5e_dt__play_scene")
            result = await client.call_tool("ur5e_dt__play_scene", {})
            after = next(t for t in (await client.list_tools()).tools
                         if t.name == "ur5e_dt__play_scene")
            assert before == after
            return result, before

    return asyncio.run(run())


@pytest.mark.parametrize("error", [
    ConnectionResetError("connection reset during response"),
    BrokenPipeError("bridge closed the pipe"),
    TimeoutError("no response within 300s; execution may continue"),
    ConnectionRefusedError("bridge exited"),
    BridgeError("native command failed", {"effects": "may have partially applied"}),
])
def test_transport_and_bridge_errors_keep_text_but_set_mcp_error(error):
    result, tool = invoke(error=error)
    text = result.content[0].text
    assert result.isError
    assert str(error) in text
    assert "was not run" not in text
    assert result.structuredContent == {"result": text}
    assert tool.outputSchema["properties"]["result"]["type"] == "string"
    if isinstance(error, ConnectionRefusedError):
        assert "may have run" in text and "use_bridge" in text


def test_explicit_legacy_application_failure_is_an_mcp_error():
    payload = {"status": "error", "message": "invalid quaternion",
               "effects": "no pose was written"}
    result, _ = invoke(value=payload)
    assert result.isError
    assert json.loads(result.content[0].text) == payload
    assert result.structuredContent == {"result": result.content[0].text}


@pytest.mark.parametrize("payload", [
    {"status": "success", "message": "Error: is just text in a successful result"},
    {"output": "", "result": {"status": "error", "message": "script data"}},
    "Error: a string returned as data by the bridge",
    ["error", "success"], None,
])
def test_success_payloads_and_script_data_keep_their_previous_shape(payload):
    result, _ = invoke(value=payload)
    assert not result.isError
    assert json.loads(result.content[0].text) == payload
    assert result.structuredContent == {"result": result.content[0].text}


def test_real_tcp_bridge_application_and_builtin_error_envelopes(monkeypatch, tmp_path):
    from bridge_testing import LiveBridge, td

    with LiveBridge() as live:
        live.bridge.register_owner("error_fixture", "error_fixture").add(
            td("refuse", lambda: {"status": "error", "message": "fixture refusal"}))
        monkeypatch.setenv("ISAAC_MCP_PORT", str(live.port))
        server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "none"})

        async def run():
            async with create_connected_server_and_client_session(server._mcp_server) as client:
                await client.initialize()
                application = await client.call_tool("error_fixture__refuse", {})
                assert application.isError
                assert json.loads(application.content[0].text)["message"] == "fixture refusal"
                path = tmp_path / "owned-fixture.bin"
                native = await client.call_tool("put_file", {
                    "path": str(path), "content_b64": "!invalid-base64!"})
                assert native.isError
                assert "not valid base64" in native.content[0].text
                assert not path.exists()
                payload = b'{"status":"error","message":"ordinary file content"}'
                written = await client.call_tool("put_file", {
                    "path": str(path), "content_b64": base64.b64encode(payload).decode()})
                assert not written.isError and path.read_bytes() == payload
                data = await client.call_tool("get_file", {"path": str(path)})
                assert not data.isError
                assert base64.b64decode(json.loads(data.content[0].text)["content_b64"]) == payload

        asyncio.run(run())
