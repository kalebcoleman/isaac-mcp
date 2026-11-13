"""Measure the offline host tool list without entering the server lifespan."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp"))


async def measure(env):
    from isaac_mcp.server import build_server

    server = build_server(env=env)
    rows = []
    for tool in await server.list_tools():
        data = tool.model_dump(mode="json")
        payload = {key: data.get(key) for key in ("name", "description", "inputSchema", "annotations")}
        size = len(json.dumps(payload, separators=(",", ":")))
        rows.append({"name": tool.name, "characters": size, "estimated_tokens": size // 4})
    total = sum(row["characters"] for row in rows)
    return {
        "tool_count": len(rows), "characters": total, "estimated_tokens": total // 4,
        "instructions_characters": len(server.instructions or ""),
        "tools": sorted(rows, key=lambda row: (-row["characters"], row["name"])),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", metavar="KEY=VALUE", nargs="*")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    env = dict(os.environ)
    for assignment in args.environment:
        key, separator, value = assignment.partition("=")
        if not separator or not key:
            parser.error(f"expected KEY=VALUE, got {assignment!r}")
        env[key] = value
    result = asyncio.run(measure(env))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for key in ("tool_count", "characters", "estimated_tokens", "instructions_characters"):
            print(f"{key}: {result[key]}")
        print(f"{'Tool':44} {'Characters':>10} {'Est. tokens':>12}")
        for row in result["tools"]:
            print(f"{row['name']:44} {row['characters']:10} {row['estimated_tokens']:12}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
