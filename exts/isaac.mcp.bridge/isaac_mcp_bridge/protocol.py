"""The wire format between a client and the bridge: one JSON object per line, each way.

    request   {"id": 7, "type": "sim.stage.info", "params": {...}, "token": "..."}
    reply     {"id": 7, "status": "success", "result": {...}}
              {"id": 7, "status": "error", "message": "...", "code": "READ_ONLY", ...}

``id`` is the caller's and is echoed back, so replies can be matched and several requests can share
a connection; a request without one gets a reply without one. ``token`` is needed once per
connection when the bridge requires authentication. Stdlib only.
"""

import json
import math
from typing import Any, Dict, Optional

MAX_FRAME_BYTES = 64 * 1024 * 1024


class ToolError(Exception):
    """A tool's own failure, with details that travel to the caller beside the message."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None, code: Optional[str] = None):
        super().__init__(message)
        self.details = dict(details or {})
        if code:
            self.details.setdefault("code", code)


def success(result: Any) -> Dict[str, Any]:
    return {"status": "success", "result": {} if result is None else result}


def error(message: str, **extra: Any) -> Dict[str, Any]:
    frame = {"status": "error", "message": message}
    frame.update({key: value for key, value in extra.items() if key not in frame and value is not None})
    return frame


def _plain(value: Any) -> Any:
    """JSON has no NaN or infinity; send them as strings rather than a frame strict parsers reject."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


class Base64(str):
    """Base64 text. It needs no JSON escaping, so ``encode`` copies it into the frame as it is."""


_SPLICE = "\x00base64\x00"


def encode(frame: Any) -> bytes:
    """One frame as a line of UTF-8 JSON. Values JSON cannot hold are sent as their repr.

    A file's content can be tens of megabytes of base64. Escaping that is one long call holding
    the interpreter, which the main loop would wait for, so ``Base64`` content is spliced in."""
    result = frame.get("result") if isinstance(frame, dict) else None
    content = result.get("content_b64") if isinstance(result, dict) else None
    if isinstance(content, Base64):
        head, _, tail = encode({**frame, "result": {**result, "content_b64": _SPLICE}}).partition(json.dumps(_SPLICE).encode())
        return b"".join((head, b'"', content.encode("ascii"), b'"', tail))
    try:
        text = json.dumps(frame, allow_nan=False, default=repr)
    except ValueError:
        text = json.dumps(_plain(frame), allow_nan=False, default=repr)
    return text.encode() + b"\n"


def decode(line: bytes) -> Dict[str, Any]:
    """A received line as a request or reply. Raises ValueError for anything but a JSON object."""
    frame = json.loads(line.decode("utf-8"))
    if not isinstance(frame, dict):
        raise ValueError("a frame must be a JSON object")
    return frame
