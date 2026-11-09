"""isaac_mcp.client — the reference client, exercised against a live bridge.

test_bridge.py stays raw-socket on purpose (protocol conformance: literal
bytes in, literal envelope out — a symmetric client/server bug can't hide
there). This suite covers the client every consumer actually ships.
"""

import json
import os
import subprocess
import sys

import pytest

from isaac_mcp.client import BridgeClient, BridgeError, call
from test_bridge import LiveBridge, td

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def live():
    with LiveBridge() as lb:
        lb.bridge.register_owner("A", "demo").add(
            td("ping", lambda message="pong": {"echo": message},
               params={"message": {"type": "string"}}))
        yield lb


# ==================== one-shot ====================

def test_oneshot_call(live):
    assert call("demo.ping", {"message": "hi"}, port=live.port) == {"echo": "hi"}


def test_oneshot_bridge_error_carries_details(live):
    with pytest.raises(BridgeError) as ei:
        call("demo.nope", port=live.port)
    assert "unknown tool" in str(ei.value)
    assert "demo.ping" in ei.value.details["known_tools"]


def test_missing_port_is_loud(monkeypatch, tmp_path):
    # No env, no explicit port, and an EMPTY runtime dir (no bridge to discover)
    # -> resolution must fail loudly rather than guess.
    monkeypatch.delenv("UR5E_DT_PORT", raising=False)
    monkeypatch.delenv("ISAAC_MCP_PORT", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    with pytest.raises(RuntimeError):
        call("list_tools")


def test_port_from_env(live, monkeypatch):
    monkeypatch.delenv("UR5E_DT_PORT", raising=False)
    monkeypatch.setenv("ISAAC_MCP_PORT", str(live.port))
    assert "demo.ping" in call("list_tools")["tools"]


def test_twin_port_env_wins_over_generic(live, monkeypatch):
    # UR5E_DT_PORT names the twin bridge; ISAAC_MCP_PORT is the generic
    # fallback. The twin variable must win when both are set.
    monkeypatch.setenv("UR5E_DT_PORT", str(live.port))
    monkeypatch.setenv("ISAAC_MCP_PORT", "1")
    assert "demo.ping" in call("list_tools")["tools"]


# ==================== persistent ====================

def test_persistent_client_reuses_socket(live):
    with BridgeClient(port=live.port) as c:
        c.call("demo.ping")
        sock = c._sock
        c.call("demo.ping")
        assert c._sock is sock  # same connection across calls


def test_reconnects_after_bridge_restart(live):
    c = BridgeClient(port=live.port)
    assert c.call("demo.ping") == {"echo": "pong"}
    # Kit restart: bridge goes away and a new one binds the same port.
    live.bridge.stop()
    import test_bridge
    live.bridge = test_bridge.make_bridge(live.loop)
    live.bridge.register_owner("A", "demo").add(td("ping", lambda: {"back": True}))
    live.bridge.start(live.port)
    # Health check must detect the dead socket and reconnect transparently.
    assert c.call("demo.ping") == {"back": True}
    c.close()


# ==================== CLI ====================

def _cli(args, port):
    env = dict(os.environ, PYTHONPATH=REPO_ROOT, ISAAC_MCP_PORT=str(port))
    return subprocess.run([sys.executable, "-m", "isaac_mcp.client", *args],
                          capture_output=True, text=True, env=env, timeout=30)


def test_cli_success_exit_0(live):
    r = _cli(["demo.ping", '{"message": "cli"}'], live.port)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"echo": "cli"}


def test_cli_bridge_error_exit_1(live):
    r = _cli(["demo.nope"], live.port)
    assert r.returncode == 1
    assert "unknown tool" in r.stderr


def test_cli_unreachable_exit_2(live):
    with __import__("socket").socket() as probe:  # a port nobody serves
        probe.bind(("localhost", 0))
        dead_port = probe.getsockname()[1]
    r = _cli(["list_tools"], dead_port)
    assert r.returncode == 2


def test_cli_bad_params_exit_2(live):
    r = _cli(["demo.ping", "not-json"], live.port)
    assert r.returncode == 2


# ==================== import weight ====================

def test_client_imports_without_mcp_sdk():
    """The client layer must import in an environment with no `mcp` package;
    only touching the server layer requires the SDK."""
    code = (
        "import sys\n"
        "class _Block:\n"
        "    def find_spec(self, name, *a, **k):\n"
        "        if name == 'mcp' or name.startswith('mcp.'):\n"
        "            raise ImportError('mcp SDK blocked by test')\n"
        "sys.meta_path.insert(0, _Block())\n"
        "import isaac_mcp, isaac_mcp.client\n"                 # must succeed
        "isaac_mcp.client.BridgeError('x')\n"
        "try:\n"
        "    isaac_mcp.server\n"                              # must require the SDK
        "except ImportError:\n"
        "    print('OK')\n"
        "else:\n"
        "    print('SERVER IMPORTED WITHOUT SDK')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, env=dict(os.environ, PYTHONPATH=REPO_ROOT),
                       timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "OK"


# ==================== port discovery ====================

def test_runtime_dir_derivation_pinned(monkeypatch, tmp_path):
    """Bridge and client derive the SAME runtime dir — the cross-process
    contract is pinned here, not shared by import."""
    from isaac_mcp_bridge import bridge as bridge_mod
    from isaac_mcp import client as client_mod
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert bridge_mod._runtime_dir() == client_mod._runtime_dir()
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    assert bridge_mod._runtime_dir() == client_mod._runtime_dir()


def test_discovery_single_bridge_zero_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("ISAAC_MCP_PORT", raising=False)
    with LiveBridge() as live:
        live.bridge.register_owner("A", "demo").add(td("ping", lambda: {"ok": 1}))
        # portfile advertised
        files = os.listdir(os.path.join(str(tmp_path), "isaac-mcp"))
        assert files and files[0].endswith(f"-{live.port}.json")
        # no port, no env -> discovered
        assert call("demo.ping") == {"ok": 1}
    # bridge stopped -> portfile removed -> resolution is loud again
    assert os.listdir(os.path.join(str(tmp_path), "isaac-mcp")) == []
    with pytest.raises(RuntimeError, match="no bridge found"):
        call("demo.ping")


def test_discovery_multiple_bridges_is_loud(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("ISAAC_MCP_PORT", raising=False)
    with LiveBridge() as a, LiveBridge() as b:
        assert a.port != b.port
        with pytest.raises(RuntimeError, match="multiple bridges"):
            call("list_tools")
        # explicit port still cuts through the ambiguity
        assert "run_python" in call("list_tools", port=a.port)["tools"]


def test_cli_list_bridges(monkeypatch, tmp_path):
    env_dir = str(tmp_path)
    with LiveBridge() as live:
        # write portfiles under our isolated runtime dir for the subprocess
        monkeypatch.setenv("XDG_RUNTIME_DIR", env_dir)
        from isaac_mcp_bridge import bridge as bridge_mod
        bridge_mod._write_portfile("localhost", live.port)
        env = dict(os.environ, PYTHONPATH=REPO_ROOT, XDG_RUNTIME_DIR=env_dir)
        env.pop("ISAAC_MCP_PORT", None)
        r = subprocess.run([sys.executable, "-m", "isaac_mcp.client", "--list-bridges"],
                           capture_output=True, text=True, env=env, timeout=30)
        assert r.returncode == 0, r.stderr
        listed = json.loads(r.stdout)
        assert listed and listed[0]["port"] == live.port


def test_discovery_uses_advertised_host(monkeypatch, tmp_path):
    """Host and port resolve from the SAME portfile: a bridge bound to a
    non-loopback-default address (e.g. a tailnet IP) advertises that host, and
    a no-arg call() must dial it — not take the port and dial 127.0.0.1."""
    alias = "127.0.0.2"   # loopback alias: bindable on Linux without setup
    probe = __import__("socket").socket()
    try:
        probe.bind((alias, 0))
    except OSError:
        pytest.skip(f"loopback alias {alias} not bindable on this platform")
    finally:
        probe.close()

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("ISAAC_MCP_PORT", raising=False)
    monkeypatch.delenv("ISAAC_MCP_HOST", raising=False)
    import asyncio, threading
    import test_bridge
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    bridge = test_bridge.make_bridge(loop)
    bridge.register_owner("A", "demo").add(td("ping", lambda: {"ok": 1}))
    try:
        bridge.start(0, host=alias)          # NOT reachable on 127.0.0.1
        from isaac_mcp.client import discover_bridges
        assert discover_bridges()[0]["host"] == alias
        assert call("demo.ping") == {"ok": 1}   # no host, no port -> discovered pair
    finally:
        bridge.stop()
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()


def test_host_env_is_honored(live, monkeypatch):
    """$ISAAC_MCP_HOST is the dial override between explicit and discovery."""
    monkeypatch.setenv("ISAAC_MCP_HOST", "localhost")
    monkeypatch.setenv("ISAAC_MCP_PORT", str(live.port))
    assert call("demo.ping", {"message": "env"}) == {"echo": "env"}


def test_ephemeral_bind_and_discovery(monkeypatch, tmp_path):
    """port=0 binds an OS-assigned port, learns it, advertises it — the
    same-app-second-instance fallback path."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("ISAAC_MCP_PORT", raising=False)
    import asyncio, threading
    import test_bridge
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    bridge = test_bridge.make_bridge(loop)
    try:
        bridge.start(0)
        assert bridge._port and bridge._port != 0    # real port learned
        bridges = __import__("isaac_mcp.client", fromlist=["x"]).discover_bridges()
        assert len(bridges) == 1 and bridges[0]["port"] == bridge._port
        assert "run_python" in call("list_tools")["tools"]   # zero-config reach
    finally:
        bridge.stop()
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()


@pytest.mark.parametrize('name,info,expected', [
    ('123-8766.json', {'pid': 123, 'port': 8766, 'host': 'localhost'}, True),
    ('123-8766.json', {'pid': 123, 'port': 8766}, True),
    ('tool-failures-123.log', {'pid': 123, 'message': 'diagnostic'}, False),
    ('123-8766.json', {'pid': 124, 'port': 8766}, False),
    ('123-8766.json', {'pid': 123, 'port': 8767}, False),
    ('123-8766.json', {'pid': 123, 'port': '8766'}, False),
    ('123-8766.json', {'pid': 123, 'port': 8766, 'host': ''}, False),
    ('123-65536.json', {'pid': 123, 'port': 65536}, False),
    ('1-8766.json', {'pid': True, 'port': 8766}, False),
    ('123-8766.json', [], False),
])
def test_discovery_metadata_contract_matches_bridge(name, info, expected):
    from isaac_mcp import client
    from isaac_mcp_bridge import bridge
    assert client._valid_portfile(name, info) is expected
    assert bridge._valid_portfile(name, info) is expected


def test_diagnostic_log_not_listed_or_deleted_as_a_bridge(monkeypatch, tmp_path):
    from isaac_mcp import client
    from isaac_mcp_bridge import bridge, builtins
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))
    with LiveBridge() as live:
        runtime = tmp_path / 'isaac-mcp'
        # The first failure is a valid JSON document with a live PID, exactly
        # the shape that previously leaked through the native list_bridges.
        failure = runtime / f'tool-failures-{os.getpid()}.log'
        payload = json.dumps({'pid': os.getpid(), 'error_type': 'ToolError', 'message': 'failed'})
        failure.write_text(payload + '\n')
        malformed = runtime / f'{os.getpid()}-65536.json'
        malformed.write_text(json.dumps({'pid': os.getpid(), 'port': 65536}))
        assert [x['port'] for x in builtins.cmd_list_bridges(live.bridge)['bridges']] == [live.port]
        assert [x['port'] for x in client.discover_bridges()] == [live.port]
        bridge._sweep_stale_portfiles()
        assert failure.read_text() == payload + '\n'
        assert malformed.exists()


# ==================== the form the host's messages recommend ====================

def test_client_file_runs_on_a_python_without_the_package(tmp_path):
    """`python3 mcp/isaac_mcp/client.py --list-bridges` is what server.LIST_BRIDGES_HELP recommends;
    -S leaves out site-packages, as on a stock python3 with no isaac_mcp or mcp installed."""
    script = os.path.join(os.path.dirname(REPO_ROOT), "mcp", "isaac_mcp", "client.py")
    env = {**os.environ, "XDG_RUNTIME_DIR": str(tmp_path)}
    env.pop("PYTHONPATH", None)
    listed = subprocess.run([sys.executable, "-S", script, "--list-bridges"], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    assert listed.returncode == 2 and listed.stdout.strip() == "[]", listed.stderr
    usage = subprocess.run([sys.executable, "-S", script, "--help"], cwd=tmp_path, env=env,
                           capture_output=True, text=True, timeout=30)
    assert usage.returncode == 0 and "-m isaac_mcp.client" not in usage.stdout


def test_module_form_keeps_its_usage():
    usage = subprocess.run([sys.executable, "-m", "isaac_mcp.client", "--help"],
                           capture_output=True, text=True, timeout=30)
    assert usage.returncode == 0 and "python3 -m isaac_mcp.client" in usage.stdout


# ---- connection errors say where the bridge was expected and whether a tool could have run ----

def _closed_port():
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_refused_connection_names_host_port_and_how_to_start_it():
    from isaac_mcp import client

    port = _closed_port()
    with pytest.raises(ConnectionRefusedError) as exc:
        client.call("status", host="127.0.0.1", port=port, timeout=2)
    text = str(exc.value)
    assert f"bridge not reachable at 127.0.0.1:{port}" in text and "./run.sh --headless" in text and "--list-bridges" in text


def test_connect_timeout_says_no_tool_ran(monkeypatch):
    import socket

    from isaac_mcp import client

    def slow(*args, **kwargs):
        raise socket.timeout("timed out")

    monkeypatch.setattr(client.socket, "create_connection", slow)
    with pytest.raises(client.BridgeConnectTimeout, match=r"127\.0\.0\.1:9.*did not accept a connection within 3s.*no tool ran"):
        client.call("status", host="127.0.0.1", port=9, timeout=3)
    assert issubclass(client.BridgeConnectTimeout, TimeoutError)  # the gateway still treats it as a timeout
