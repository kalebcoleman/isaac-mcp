#!/usr/bin/env python3
"""One-line isaac-mcp health report for an agent's SessionStart hook.

Prints whether a simulator bridge answers, and reminds the agent to use the
Isaac MCP tools. Always exits 0 so it can never block a session.

    python3 scripts/session_check.py [--port N]
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp"))


def report(port=None):
    from isaac_mcp import client
    try:
        client.call("status", {}, port=port, timeout=3)
        state = "a simulator bridge answers"
    except Exception as exc:  # unreachable, several bridges, or an error envelope
        state = f"no bridge answered ({type(exc).__name__}); start one with sim__launch or ./run.sh"
    return ("isaac MCP: " + state + ". Load its tools with ToolSearch 'mcp__isaac', call isaac_help(task), and use them for "
            "screenshots, recordings, contacts and Isaac Lab runs before writing your own scripts.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=int(os.environ["ISAAC_MCP_PORT"]) if os.environ.get("ISAAC_MCP_PORT") else None)
    args = ap.parse_args(argv)
    try:
        print(report(args.port))
    except Exception as exc:
        print(f"isaac MCP: check failed ({type(exc).__name__}). Still try the mcp__isaac tools first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
