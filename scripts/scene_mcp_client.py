"""Real stdio MCP client for manual/agent use. No bridge or private Kit APIs.

Usage: python scripts/scene_mcp_client.py --port 18861 sim__stage__info '{}'
       python scripts/scene_mcp_client.py --port 18861 list_tools   # every tool, with its arguments'
Uses this checkout's host server. ISAAC_MCP_ASSET_CONFIG is user launch configuration.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def parameters(port):
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "isaac_mcp.server"],
        env={
            # Defaults for a scene-only client; an exported value opts in (e.g. default,+input).
            "ISAAC_MCP_TOOLSETS": "default,-code",
            **os.environ,
            "PYTHONPATH": str(ROOT / "mcp"),
            "ISAAC_MCP_PORT": str(port),
            "UR5E_DT_PORT": str(port),
        },
    )


async def call(client, name, args):
    result = await client.call_tool(name, args)
    data = "\n".join(x.text for x in result.content if x.type == "text")
    try:
        data = json.loads(data)
    except ValueError:
        pass
    return dict(tool=name, arguments=args, isError=result.isError, result=data)


async def main(port, name, args):
    async with stdio_client(parameters(port)) as (r, w):
        async with ClientSession(r, w) as c:
            await c.initialize()
            if name == "list_tools":
                return (await c.list_tools()).model_dump(mode="json")
            reply = await call(c, name, args)
            if reply["isError"] and str(reply["result"]).startswith("Unknown tool"):
                reply["hint"] = "Run this script with the tool name list_tools to see every tool and its arguments."
            return reply


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, required=True)
    p.add_argument("tool")
    p.add_argument("arguments", nargs="?", default="{}")
    a = p.parse_args()
    print(
        json.dumps(asyncio.run(main(a.port, a.tool, json.loads(a.arguments))), indent=2)
    )
