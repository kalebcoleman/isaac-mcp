"""Gateway: bad ports, busy-versus-dead bridge status and garbled replies."""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("port", [0, -1, 65536, True, "18000"])
def test_use_bridge_refuses_a_bad_port_without_dialling(port):
    import asyncio

    from isaac_mcp import server as server_module

    result = asyncio.run(server_module._use_bridge(server_module.FastMCP("t"), port))
    assert result.startswith("Error: port must be a number")



def test_sim_status_tells_a_busy_bridge_from_a_dead_one(monkeypatch):
    from isaac_mcp import client, sim_sessions

    def busy(*args, **kwargs):
        raise client.BridgeError("DOORWAY_BUSY")

    monkeypatch.setattr(client, "call", busy)
    assert sim_sessions._bridge_state("127.0.0.1", 1, 1)[0] == "busy"

    def dead(*args, **kwargs):
        raise OSError("refused")

    monkeypatch.setattr(client, "call", dead)
    assert sim_sessions._bridge_state("127.0.0.1", 1, 1)[0] == "starting_or_unresponsive"



def test_a_garbled_bridge_reply_disconnects_instead_of_desynchronising():
    import itertools
    import threading

    from isaac_mcp import client

    class Sock:
        def __init__(self):
            self.chunks = [b'{"status":', b' \xff\n{"id": 1, "status": "success", "result": {}}\n']
            self.closed = False

        def settimeout(self, _):
            pass

        def sendall(self, _):
            pass

        def recv(self, _):
            return self.chunks.pop(0)

        def close(self):
            self.closed = True

    sock = Sock()
    bridge = client.BridgeClient.__new__(client.BridgeClient)
    bridge._sock, bridge._buffer, bridge._token, bridge.timeout = sock, b"", None, 1.0
    bridge._ids, bridge._lock = itertools.count(1), threading.Lock()
    bridge._dropped = lambda: False
    with pytest.raises(ValueError):
        bridge.call("status")
    assert sock.closed and bridge._sock is None, "the next call starts on a fresh connection, not on the leftover line"
