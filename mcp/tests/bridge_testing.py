"""Helpers for tests that need a bridge: one in-process, or one listening on a real socket."""

import asyncio
import json
import socket
import threading

from twinsmith_bridge.bridge import McpBridge, ToolDefinition


def make_bridge(loop=None, **options):
    """A bridge whose calls run on ``loop`` (a loop on another thread), or are only created."""
    options.setdefault("auth", False)
    if loop is None:
        return McpBridge(schedule_coroutine=lambda coroutine: coroutine.close(), **options)
    return McpBridge(schedule_coroutine=lambda coroutine: asyncio.run_coroutine_threadsafe(coroutine, loop), **options)


def td(name, handler, params=None, **flags):
    return ToolDefinition(name=name, description=f"{name} desc", parameters=params or {}, handler=handler, **flags)


def dispatch(bridge, name, params=None):
    return asyncio.run(bridge.dispatch(name, params or {}))


class LiveBridge:
    """A bridge on its own loop thread, listening on a free loopback port."""

    def __init__(self, **options):
        self.options = options

    def __enter__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.bridge = make_bridge(self.loop, **self.options)
        self.port = self.bridge.start(0)
        return self

    def __exit__(self, *exc):
        self.bridge.stop()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)
        self.loop.close()

    def client(self):
        return socket.create_connection(("localhost", self.port), timeout=5)


def send(sock, frame):
    sock.sendall(json.dumps(frame).encode() + b"\n")


def receive(sock, buffer=b""):
    """One reply line from the socket. Returns (reply, bytes read past it)."""
    while b"\n" not in buffer:
        chunk = sock.recv(65536)
        assert chunk, "connection closed before a reply"
        buffer += chunk
    line, rest = buffer.split(b"\n", 1)
    return json.loads(line), rest


def roundtrip(sock, frame):
    send(sock, frame)
    return receive(sock)[0]
