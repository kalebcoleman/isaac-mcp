"""Final destructive clear checks in the explicitly selected disposable stage."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from scene_mcp_client import call, parameters


async def validate(port, output):
    records = []
    async with stdio_client(parameters(port)) as (r, w):
        async with ClientSession(r, w) as client:
            await client.initialize()

            async def invoke(name, error=False, **args):
                item = await call(client, name, args)
                records.append(item)
                Path(output).write_text(json.dumps(records, indent=2))
                assert item["isError"] == error, item
                return item["result"]

            info = await invoke("sim__stage__info")
            assert info["stopped"], info
            t = {k: info[k] for k in ("session_id", "stage_id")}
            found = [x["path"] for x in (await invoke("sim__stage__tree", **t, limit=200))["nodes"]]
            if "/World/TestCamera" not in found or "/World/Agilebot" not in found:
                raise SystemExit("Run validate_scene_equivalents.py first: this checks the stage it leaves, including "
                                 "/World/Agilebot from the approved root 'agilebot' (see docs/TOOLS-SCENE.md). Nothing was changed.")
            await invoke(
                "sim__prim__delete",
                error=True,
                **t,
                path="/World/Parent/Robot",
                confirm=True,
            )
            out = await invoke("sim__stage__clear_owned", **t, confirm=True)
            assert out["refused"] and out["removed"], out
            assert any(x["path"] == "/World/TestCamera" for x in out["refused"])
            tree = await invoke("sim__stage__tree", **t, limit=200)
            names = [x["path"] for x in tree["nodes"]]
            assert "/World/TestCamera" in names and "/World/Agilebot" in names
            for removed in out["removed"]:
                assert (removed["deleted"] in names) == removed["still_composed"]
            print(json.dumps({"passed": True, "calls": len(records), "target": t}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    asyncio.run(validate(args.port, args.output))
