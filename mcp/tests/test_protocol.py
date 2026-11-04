"""protocol.py — NDJSON framing + envelope."""

import json

import pytest

from isaac_mcp_bridge import protocol


def test_encode_frame_is_one_line():
    data = protocol.encode_frame({"a": 1, "s": "x\ny"})
    assert data.endswith(b"\n")
    assert data.count(b"\n") == 1  # embedded newlines are escaped by json
    assert json.loads(data.decode()) == {"a": 1, "s": "x\ny"}


def test_encode_frame_falls_back_to_repr():
    class Odd:
        def __repr__(self):
            return "<odd>"

    decoded = json.loads(protocol.encode_frame({"v": Odd()}).decode())
    assert decoded["v"] == "<odd>"


def test_decoder_multiple_and_partial_frames():
    d = protocol.FrameDecoder()
    out = list(d.feed(b'{"a": 1}\n{"b": 2}\n{"c":'))
    assert out == [({"a": 1}, None), ({"b": 2}, None)]
    out = list(d.feed(b" 3}\n"))
    assert out == [({"c": 3}, None)]


def test_decoder_bad_line_is_isolated():
    d = protocol.FrameDecoder()
    out = list(d.feed(b'not json\n{"ok": true}\n'))
    assert out[0][0] is None and "invalid JSON" in out[0][1]
    assert out[1] == ({"ok": True}, None)


def test_decoder_skips_blank_lines():
    d = protocol.FrameDecoder()
    assert list(d.feed(b"\n\n{}\n")) == [({}, None)]


def test_envelopes():
    assert protocol.success({"x": 1}) == {"status": "success", "result": {"x": 1}}
    assert protocol.success(None) == {"status": "success", "result": {}}
    err = protocol.error("boom", traceback="tb", status="ignored", nothing=None)
    assert err == {"status": "error", "message": "boom", "traceback": "tb"}


def test_tool_error_carries_details():
    e = protocol.ToolError("bad", details={"output": "partial"})
    assert str(e) == "bad" and e.details == {"output": "partial"}


def test_frame_decoder_drops_an_oversized_frame_and_keeps_going():
    from isaac_mcp_bridge.protocol import FrameDecoder, encode_frame

    decoder = FrameDecoder(max_frame=1024)
    assert [e for _, e in decoder.feed(b"x" * 2000)] == ["frame longer than the 0 MiB limit was dropped"]
    assert list(decoder.feed(b"yyy")) == []  # still inside the dropped frame
    assert [o for o, _ in decoder.feed(b'yyy\n{"a": 1}\n')] == [{"a": 1}]
    assert list(decoder.feed(b'{"b"')) == [] and [o for o, _ in decoder.feed(b': 2}\n')] == [{"b": 2}]
    assert encode_frame({"x": float("nan")}) == b'{"x": "nan"}\n'
    assert [e for _, e in FrameDecoder(max_frame=8).feed(b'{"a": 11111}\n')] == ["frame longer than the 0 MiB limit was dropped"]


def test_a_deeply_nested_frame_is_refused_and_the_decoder_keeps_working():
    from isaac_mcp_bridge.protocol import FrameDecoder

    decoder = FrameDecoder()
    (_, error), = decoder.feed(b"[" * 200000 + b"\n")
    assert "invalid JSON frame" in error
    assert [o for o, _ in decoder.feed(b'{"a": 1}\n')] == [{"a": 1}]
