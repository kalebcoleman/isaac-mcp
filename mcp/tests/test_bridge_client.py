"""The bridge client: finding a bridge, matching replies to requests, and what each failure looks like."""

import json
import socket
import threading
import time

import pytest

from bridge_testing import LiveBridge, td
from isaac_mcp import client as cl
from isaac_mcp.client import BridgeClient, BridgeError


@pytest.fixture(autouse=True)
def no_configured_port(monkeypatch):
    for name in ("ISAAC_MCP_PORT", "UR5E_DT_PORT", "ISAAC_MCP_HOST", "ISAAC_MCP_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def test_one_connection_carries_many_calls_and_errors_keep_their_details():
    with LiveBridge() as live:
        live.bridge.register_owner("pack", "demo").add(td("echo", lambda text="": {"text": text}))
        with BridgeClient(port=live.port, timeout=5) as bridge:
            assert bridge.call("demo.echo", {"text": "a"}) == {"text": "a"}
            first = bridge._sock
            assert bridge.call("demo.echo", {"text": "b"}) == {"text": "b"} and bridge._sock is first
            with pytest.raises(BridgeError, match="unknown tool") as error:
                bridge.call("demo.nope")
            assert "demo.echo" in error.value.details["known_tools"] and error.value.details["code"] == "UNKNOWN_TOOL"
        assert cl.call("demo.echo", {"text": "c"}, port=live.port, timeout=5) == {"text": "c"}


def test_with_no_port_given_the_one_advertised_bridge_is_used_and_several_are_refused():
    with pytest.raises(RuntimeError, match="no bridge found"):
        BridgeClient().call("status")
    with LiveBridge() as live:
        assert BridgeClient(timeout=5).call("status")["port"] == live.port
        assert cl.discover_bridges()[0]["port"] == live.port
        with LiveBridge() as second:
            with pytest.raises(RuntimeError, match="multiple bridges running") as error:
                BridgeClient().call("status")
            assert str(live.port) in str(error.value) and str(second.port) in str(error.value)
            assert BridgeClient(port=second.port, timeout=5).call("status")["port"] == second.port
    assert cl.discover_bridges() == []


def test_a_configured_port_wins_over_discovery(monkeypatch):
    with LiveBridge() as live, LiveBridge() as other:
        monkeypatch.setenv("ISAAC_MCP_PORT", str(other.port))
        assert BridgeClient(timeout=5).call("status")["port"] == other.port
        monkeypatch.setenv("UR5E_DT_PORT", str(live.port))
        assert BridgeClient(timeout=5).call("status")["port"] == live.port, "the twin's variable is read first"


def test_a_reply_that_arrives_after_its_call_timed_out_is_not_taken_for_the_next_one():
    with LiveBridge() as live:
        gate = threading.Event()

        async def slow(tag):
            import asyncio

            while not gate.is_set():
                await asyncio.sleep(0.005)
            return {"tag": tag}

        registrar = live.bridge.register_owner("pack", "demo")
        registrar.add(td("slow", slow, {"tag": {"type": "string"}}))
        registrar.add(td("fast", lambda: {"tag": "fast"}))
        bridge = BridgeClient(port=live.port, timeout=5)
        with pytest.raises(TimeoutError, match="may still be running"):
            bridge.call("demo.slow", {"tag": "late"}, timeout=0.1)
        assert bridge._sock is None, "the connection that still owes a reply is not reused"
        gate.set()
        assert bridge.call("demo.fast") == {"tag": "fast"}


def test_a_connection_the_bridge_closed_while_idle_is_reopened_without_losing_a_call():
    with LiveBridge() as live:
        bridge = BridgeClient(port=live.port, timeout=5)
        assert bridge.call("status")["port"] == live.port
        for connection in list(live.bridge._connections):
            connection.close()
        time.sleep(0.05)
        assert bridge.call("status")["port"] == live.port


def test_a_connection_lost_mid_call_says_the_call_may_have_run():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()

    def serve():
        peer, _ = listener.accept()
        peer.recv(4096)
        peer.close()

    port = listener.getsockname()[1]
    threading.Thread(target=serve, daemon=True).start()
    with pytest.raises(ConnectionError, match="may have run"):
        BridgeClient(port=port, timeout=5).call("sim.prim.create", {"path": "/World/A"})
    listener.close()
    with pytest.raises(ConnectionRefusedError):
        BridgeClient(port=port, timeout=5).call("status")


def test_advertisements_in_a_folder_others_can_write_to_are_ignored(tmp_path):
    with LiveBridge():
        folder = tmp_path / "xdg" / "isaac-mcp"
        assert len(cl.discover_bridges()) == 1
        folder.chmod(0o777)
        assert cl.discover_bridges() == [], "another account could have planted a bridge there"
        folder.chmod(0o700)
        (folder / "notes.json").write_text("{}")
        (folder / "12-34.json").write_text(json.dumps({"pid": 99, "port": 34}))   # says another pid inside
        assert len(cl.discover_bridges()) == 1


def test_the_command_line_lists_bridges_and_calls_tools(capsys):
    assert cl.main(["--list-bridges"]) == 2 and json.loads(capsys.readouterr().out) == []
    with LiveBridge() as live:
        assert cl.main(["--list-bridges"]) == 0 and json.loads(capsys.readouterr().out)[0]["port"] == live.port
        assert cl.main(["status", "--port", str(live.port)]) == 0 and json.loads(capsys.readouterr().out)["port"] == live.port
        assert cl.main(["nope", "--port", str(live.port)]) == 1 and "unknown tool" in capsys.readouterr().err
        assert cl.main(["status", "not json", "--port", str(live.port)]) == 2
        assert cl.main(["status", "[1]", "--port", str(live.port)]) == 2
    assert cl.main([]) == 2
    assert cl.main(["status", "--port", "1"]) == 2
