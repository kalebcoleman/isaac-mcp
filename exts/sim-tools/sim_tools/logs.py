"""Bounded, file-based reading of Kit's log. Never installs a log callback.

Kit's log file is written by carb. Reading it here cannot deadlock the app; registering an
``omni.log``/``carb.logging`` consumer or subscribing to Kit's log event stream has done so in
other servers (S13), and the pack guard test forbids those names.
"""

import os
import re
from pathlib import Path

from isaac_mcp.project_assets import refuse

READ_CAP = 1024 * 1024  # 1 MiB per call
MESSAGE_CAP = 2048
LINE_CAP = 512
RESPONSE_BUDGET = 40000  # bytes of messages; keeps the whole reply under the 64 KiB envelope
CHANNEL_CAP = 40

LEVELS = {"verbose": 0, "debug": 0, "info": 1, "warning": 2, "error": 3, "fatal": 4}
LEVEL_NAMES = {0: "debug", 1: "info", 2: "warning", 3: "error", 4: "fatal"}
HEADER = re.compile(
    r"^(\S+) (?:\[[\d.,]+ms\] )?\[(Verbose|Debug|Info|Warning|Error|Fatal)\] "
    r"\[([^\]]*)\] ?(.*)$"
)
SECRET = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|authorization)\b\s*[:=]\s*\S+"
)
BEARER = re.compile(r"(?i)\bBearer\s+\S+")


def log_path():
    import carb.settings

    log = carb.settings.get_settings().get("/log/file")
    if not log or not Path(log).is_file():
        refuse("LOG_UNAVAILABLE", "Kit did not expose an existing /log/file.")
    return str(log)


def redact(text):
    """Mask obvious credential-shaped text; never claims to find every secret."""
    text = BEARER.sub("Bearer <redacted>", text)
    return SECRET.sub(lambda match: match.group(1) + "=<redacted>", text)


def _read(path, start, length):
    with open(path, "rb") as stream:
        stream.seek(start)
        return stream.read(length)


def _aligned_start(data, start):
    """Drop a partial first line when reading from the middle of a file."""
    if start == 0:
        return data, 0
    newline = data.find(b"\n")
    if newline < 0:
        return b"", start + len(data)
    return data[newline + 1:], start + newline + 1


def default_lines(lines, limit):
    """The original sim.logs.tail filter, unchanged apart from the larger window."""
    picked = [line[:LINE_CAP] for line in lines
              if "Warning" in line or "Error" in line or "[sim-tools]" in line]
    return picked[-limit:]


def parse(data, start=0):
    """Entries from *data*; continuation lines belong to the previous entry."""
    entries = []
    offset = start
    for line in data.splitlines(keepends=True):
        text = line.decode(errors="replace").rstrip("\r\n")
        match = HEADER.match(text)
        if match:
            timestamp, level, channel, message = match.groups()
            entries.append(dict(
                timestamp=timestamp,
                level=LEVEL_NAMES[LEVELS[level.lower()]],
                channel=channel,
                message=message,
                offset=offset,
                end=offset + len(line),
            ))
        elif entries and text.strip():
            entries[-1]["message"] += "\n" + text
            entries[-1]["end"] = offset + len(line)
        offset += len(line)
    return entries


def _entry(entry):
    message = redact(entry["message"])
    truncated = len(message) > MESSAGE_CAP
    if truncated:
        message = message[:MESSAGE_CAP]
    return dict(
        timestamp=entry["timestamp"],
        level=entry["level"],
        channel=entry["channel"],
        message=message,
        offset=entry["offset"],
        message_truncated=truncated,
    )


def _cursor(path, offset, stat):
    return dict(
        offset=int(offset),
        file=str(path),
        device=int(stat.st_dev),
        inode=int(stat.st_ino),
        size=int(stat.st_size),
        mtime_ns=int(stat.st_mtime_ns),
    )


def mark():
    path = log_path()
    stat = os.stat(path)
    return dict(
        cursor=_cursor(path, stat.st_size, stat),
        file=str(path),
        offset=int(stat.st_size),
        note="Pass this cursor to sim__logs__tail or sim__logs__summary to read only later lines.",
    )


def _validated_cursor(value):
    if not isinstance(value, dict):
        refuse("INVALID_CURSOR", "cursor must be the object returned by sim__logs__mark or next_cursor.")
    required = ("offset", "file", "device", "inode", "size")
    if any(key not in value for key in required) or any(
        type(value[key]) is not int for key in ("offset", "device", "inode", "size")
    ) or not isinstance(value["file"], str):
        refuse(
            "INVALID_CURSOR",
            "cursor is missing its byte offset or file identity. Take a fresh one with sim__logs__mark.",
        )
    if value["offset"] < 0 or value["offset"] > value["size"]:
        refuse("INVALID_CURSOR", "cursor offset is outside the file. Take a fresh one with sim__logs__mark.")
    return value


def _window(cursor):
    """(path, start, end, stat, aligned) for a cursor or the retained window."""
    path = log_path()
    stat = os.stat(path)
    if cursor is None:
        return path, max(0, stat.st_size - READ_CAP), stat.st_size, stat, True
    cursor = _validated_cursor(cursor)
    if cursor["file"] != str(path):
        refuse(
            "LOG_CHANGED",
            f"Kit's log is now {path}, not the cursor's {cursor['file']}. Take a fresh cursor with "
            "sim__logs__mark.",
        )
    if (
        cursor["device"] != stat.st_dev
        or cursor["inode"] != stat.st_ino
        or stat.st_size < cursor["offset"]
    ):
        refuse(
            "LOG_ROTATED",
            "Kit's log file was rotated, replaced or truncated since the cursor. Lines after the old "
            "offset are unrelated; take a fresh cursor with sim__logs__mark.",
            cursor_offset=cursor["offset"],
            current_size=stat.st_size,
        )
    return path, cursor["offset"], stat.st_size, stat, False


def _level_ok(entry, minimum):
    return LEVELS[entry["level"]] >= LEVELS[minimum]


def tail(args):
    """sim.logs.tail: the original fields, plus structured entries for the new filters."""
    limit = args.get("limit", 50)
    if type(limit) is not int or not 1 <= limit <= 100:
        refuse("LIMIT", "limit 1..100 required.")
    minimum = args.get("min_level", "warning")
    if minimum not in LEVELS:
        refuse("INVALID_VALUE", "min_level must be debug, info, warning, error or fatal.")
    channel = args.get("channel", "")
    contains = args.get("contains", "")
    if not isinstance(channel, str) or len(channel) > 64:
        refuse("INVALID_VALUE", "channel must be a string of at most 64 characters.")
    if not isinstance(contains, str) or len(contains) > 128:
        refuse("INVALID_VALUE", "contains must be a string of at most 128 characters.")
    structured = any(key in args for key in ("min_level", "channel", "contains", "cursor"))
    path, start, end, stat, aligned = _window(args.get("cursor"))
    data = _read(path, start, min(READ_CAP, end - start))
    if aligned:
        data, start = _aligned_start(data, start)
    read_end = start + len(data)
    entries = parse(data, start)
    needle, channel_needle = contains.lower(), channel.lower()
    matching = [
        entry for entry in entries
        if _level_ok(entry, minimum)
        and (not channel_needle or channel_needle in entry["channel"].lower())
        and (not needle or needle in entry["message"].lower())
    ]
    result = dict(
        file=str(path),
        lines=default_lines(
            data.decode(errors="replace").splitlines(), limit
        ),
        scope="last 1 MiB; WARN/ERROR and [sim-tools] scene changes only"
        if args.get("cursor") is None
        else f"after cursor at byte {start}",
        scene_events=args["scene_events"],
    )
    if not structured:
        return result
    returned, budget, dropped = [], 0, False
    for entry in matching:
        encoded = _entry(entry)
        cost = len(encoded["message"].encode()) + 256
        if len(returned) == limit or budget + cost > RESPONSE_BUDGET:
            dropped = True
            break
        returned.append(encoded)
        budget += cost
    more_bytes = read_end < end
    if dropped and len(returned) < len(matching):
        next_offset = matching[len(returned)]["offset"]
    else:
        next_offset = read_end
    result.update(
        entries=returned,
        next_cursor=_cursor(path, next_offset, stat),
        truncated=more_bytes or dropped,
        filters=dict(
            min_level=minimum,
            channel=channel,
            contains=contains,
            limit=limit,
        ),
        scope_note="entries are oldest first from the cursor; message is capped at 2 KB; "
                   "at most 1 MiB is read per call",
    )
    return result


def summary(args):
    cursor = args.get("cursor")
    path, start, end, stat, aligned = _window(cursor)
    data = _read(path, start, min(READ_CAP, end - start))
    if aligned:
        data, start = _aligned_start(data, start)
    read_end = start + len(data)
    entries = parse(data, start)
    counts = {"debug": 0, "info": 0, "warning": 0, "error": 0, "fatal": 0}
    channels = {}
    errors = []
    for entry in entries:
        counts[entry["level"]] += 1
        channels[entry["channel"]] = channels.get(entry["channel"], 0) + 1
        if entry["level"] in ("error", "fatal"):
            errors.append(entry)
    ordered = sorted(channels.items(), key=lambda item: (-item[1], item[0]))
    more_bytes = read_end < end
    return dict(
        file=str(path),
        from_cursor=cursor is not None,
        from_byte=start,
        to_byte=read_end,
        entries=len(entries),
        counts=counts,
        channels=[dict(channel=name, count=count) for name, count in ordered[:CHANNEL_CAP]],
        channels_truncated=len(ordered) > CHANNEL_CAP,
        first_error=_entry(errors[0]) if errors else None,
        last_error=_entry(errors[-1]) if errors else None,
        next_cursor=_cursor(path, read_end, stat),
        truncated=more_bytes,
        scope="last 1 MiB" if cursor is None else f"after cursor at byte {start}",
        note="Counts are of log lines read in this window, not of every event in the simulator.",
    )
