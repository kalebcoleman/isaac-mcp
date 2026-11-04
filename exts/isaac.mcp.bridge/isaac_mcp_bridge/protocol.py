"""Wire protocol for the isaac.mcp.bridge bridge — NDJSON frames + response envelope.

Framing (NDJSON): every message is exactly one JSON object serialized on a
single line and terminated by ``\\n``. Message boundaries are therefore exact —
no parse-until-valid guessing — and one malformed line yields one error
response instead of poisoning the connection buffer.

Request : {"type": "<canonical tool name>", "params": {...}}
Response: {"status": "success", "result": <payload>}
        | {"status": "error",   "message": "...", ...extra diagnostic keys}

Handlers never speak this envelope. A tool handler returns its payload (any
JSON-serializable value; ``None`` becomes ``{}``) or raises. The bridge wraps
exactly once. ``ToolError`` lets a handler attach structured diagnostics
(e.g. run_python's captured stdout) to the error frame.
"""

import json
import math
from typing import Any, Dict, Iterator, Optional


class ToolError(Exception):
    """Handler-raised failure with optional structured diagnostics.

    ``details`` keys are merged into the error frame alongside ``message``
    (reserved keys ``status``/``message`` in details are ignored).
    """

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.details = details or {}


def success(result: Any) -> Dict[str, Any]:
    """Wrap a handler payload in the success envelope (single wrap, always)."""
    return {"status": "success", "result": {} if result is None else result}


def error(message: str, **extra: Any) -> Dict[str, Any]:
    """Build an error envelope; extra keys (traceback, output, ...) ride along."""
    frame = {"status": "error", "message": message}
    for k, v in extra.items():
        if k not in ("status", "message") and v is not None:
            frame[k] = v
    return frame


def _finite(obj: Any) -> Any:
    """Replace NaN and infinities with strings: they are not JSON, and strict parsers reject the whole frame."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite(v) for v in obj]
    return obj


def encode_frame(obj: Any) -> bytes:
    """One JSON object -> one NDJSON line (UTF-8, newline-terminated)."""
    try:
        text = json.dumps(obj, default=_json_fallback, allow_nan=False)
    except ValueError:  # NaN or infinity somewhere; only this rare frame pays for the walk
        text = json.dumps(_finite(obj), default=_json_fallback)
    return text.encode("utf-8") + b"\n"


def _json_fallback(obj: Any) -> str:
    """Last-resort serializer so a handler returning e.g. a Gf.Vec3d or numpy
    scalar degrades to its repr instead of killing the response frame."""
    return repr(obj)


MAX_FRAME_BYTES = 256 * (1 << 20)  # a request is small; files go base64 in one frame, so this stays generous


class FrameDecoder:
    """Incremental NDJSON decoder: feed raw bytes, iterate complete frames.

    Each yielded item is ``(obj, None)`` for a parsed frame or ``(None, err)``
    for a line that wasn't valid JSON — the caller answers the bad line with an
    error response and the connection stays healthy.
    """

    def __init__(self, max_frame: int = MAX_FRAME_BYTES) -> None:
        self._buffer = bytearray()
        self._max_frame = max_frame
        self._skipping = False  # inside a frame already refused as too long: drop it up to its newline

    def feed(self, data: bytes) -> Iterator[tuple]:
        if self._skipping:
            end = data.find(b"\n")
            if end < 0:
                return
            data, self._skipping = data[end + 1:], False
        scanned = len(self._buffer)  # search only new bytes, so a long frame costs linear time, not quadratic
        self._buffer += data
        while True:
            end = self._buffer.find(b"\n", scanned)
            if end < 0:
                break
            line = bytes(self._buffer[:end]).strip()
            del self._buffer[:end + 1]
            scanned = 0
            if not line:
                continue
            if len(line) > self._max_frame:  # the whole frame can arrive in one chunk, before the check below
                yield None, f"frame longer than the {self._max_frame // (1 << 20)} MiB limit was dropped"
                continue
            try:
                yield json.loads(line.decode("utf-8")), None
            except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as e:
                yield None, f"invalid JSON frame: {e}"
        if len(self._buffer) > self._max_frame:
            self._buffer.clear()
            self._skipping = True
            yield None, f"frame longer than the {self._max_frame // (1 << 20)} MiB limit was dropped"
