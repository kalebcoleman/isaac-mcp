"""Live two-server MCP isolation checks; only invalid-target writes are attempted."""

import argparse
import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import ClientSession
from mcp.client.stdio import stdio_client
from scene_mcp_client import call, parameters


async def validate(first, second, output):
    records = []
    async with AsyncExitStack() as stack:
        clients = []
        for port in (first, second):
            r, w = await stack.enter_async_context(stdio_client(parameters(port)))
            client = await stack.enter_async_context(ClientSession(r, w))
            await client.initialize()
            clients.append(client)

        async def invoke(index, name, error=False, **args):
            result = await call(clients[index], name, args)
            records.append(dict(client=index, **result))
            Path(output).write_text(json.dumps(records, indent=2))
            assert result["isError"] == error, result
            payload = result["result"]
            if error and isinstance(payload, str):
                payload = json.loads(payload[payload.index("{") :])
            return payload

        a, b = [await invoke(i, "sim__stage__info") for i in range(2)]
        assert a["pid"] != b["pid"] and a["session_id"] != b["session_id"]
        foreign = {key: a[key] for key in ("session_id", "stage_id")}
        refusal = await invoke(
            1,
            "sim__prim__create",
            error=True,
            **foreign,
            path="/World/CrossSessionMustNotAppear",
            kind="Cube",
        )
        assert refusal["code"] == "STALE_TARGET", refusal
        for index, info in enumerate((a, b)):
            result = await invoke(
                index,
                "sim__prim__get",
                error=True,
                **{key: info[key] for key in ("session_id", "stage_id")},
                path="/World/CrossSessionMustNotAppear",
            )
            assert result["code"] == "NOT_FOUND", result
        await invoke(0, "use_bridge", port=second)
        switched = await invoke(0, "sim__stage__info")
        untouched = await invoke(1, "sim__stage__info")
        assert switched["pid"] == b["pid"] == untouched["pid"]
        await invoke(0, "use_bridge", port=first)
        restored = await invoke(0, "sim__stage__info")
        untouched = await invoke(1, "sim__stage__info")
        assert restored["pid"] == a["pid"] and untouched["pid"] == b["pid"]
        print(
            json.dumps(dict(passed=True, calls=len(records), pids=[a["pid"], b["pid"]]))
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first", type=int, required=True)
    parser.add_argument("--second", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    asyncio.run(validate(args.first, args.second, args.output))
