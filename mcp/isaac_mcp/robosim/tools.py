"""Single grouped FastMCP entry point; server wiring is deliberately separate."""

import asyncio
import json
from typing import Annotated, Literal

from mcp.types import ToolAnnotations
from pydantic import Field

from .bridge import RoboSimBridge


def register_robosim_tools(server, *, bridge=None, workspace=None, root=None):
    bridge = bridge or RoboSimBridge(workspace=workspace, root=root)

    @server.tool(name="robosim", annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True,
    ))
    async def robosim(
        action: Annotated[Literal["status", "start", "scene_template", "reference", "snapshot", "compare",
                                  "rollout", "compare_take", "stop", "report"], Field(description="Action to perform.")],
        run: Annotated[str | None, Field(description="Exact run name from start; omit for status to list runs.")] = None,
        name: Annotated[str | None, Field(description="start: short run name; letters, digits, underscores and hyphens.")] = None,
        scene: Annotated[str | None, Field(description="start: absolute trusted Python scene file; copied into the run. Executes code, not sandboxed.")] = None,
        device: Annotated[Literal["cpu", "cuda:0"] | None, Field(description="Required for start: cpu or cuda:0. Later actions inherit the run's device.")] = None,
        video: Annotated[str | None, Field(description="reference: absolute local video file; copied before extracting frames.")] = None,
        keys: Annotated[list[str] | None, Field(description="compare/compare_take: RSS object masks, e.g. ['cube=red'] or ['cube=#d01a10~0.1'].")] = None,
        take: Annotated[str | None, Field(description="rollout/compare_take: take basename, without extension.")] = None,
        routine: Annotated[str | None, Field(description="rollout: routine defined by the scene; omit for RSS's default routine.")] = None,
        camera: Annotated[str | None, Field(description="Camera name from start's RSS status; omit for the scene's first camera.")] = None,
        at: Annotated[str, Field(description="reference: RSS time anchors, e.g. '0:start,1:middle,end'.")] = "0:start,end",
        every: Annotated[int, Field(description="rollout: record every N control steps.", ge=1)] = 4,
        size: Annotated[str, Field(description="snapshot/compare: WIDTHxHEIGHT pixels.")] = "320x240",
        width: Annotated[int, Field(description="reference: contact-sheet tile width.", ge=1, le=4096)] = 320,
        port: Annotated[int, Field(description="start: live viewer TCP port (RSS listens on all interfaces).", ge=1024, le=65535)] = 8080,
        wait_s: Annotated[float, Field(description="Wait at most this many seconds; poll status with run for completion.", ge=0, le=120)] = 0,
        allow_concurrent_gpu: Annotated[bool, Field(description="start: knowingly permit cuda:0 despite other GPU workloads or an unavailable GPU check.")] = False,
    ) -> str:
        """RSS live real-to-sim via its private Python: start a trusted scene, extract video frames,
        snapshot, compare masks, record a rollout, compare_take, report and stop. Runs the scene's Python
        (not sandboxed); device=cuda:0 uses the GPU. Image-space metrics only, not physics fitting. Long actions
        return a run; poll status. CPU never auto-upgrades to GPU.
        """
        arguments = locals().copy()
        arguments.pop("bridge", None)
        result = await asyncio.to_thread(bridge.call, **arguments)
        return json.dumps(result)

    return bridge
