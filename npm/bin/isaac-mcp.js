#!/usr/bin/env node
// Thin launcher: `npx isaac-mcp` runs the Python MCP server via uvx (stdio passes straight through).
// The server itself is the Python package in ../mcp; this file only finds uv and forwards arguments.
//   TWIN_MCP_FROM   override the uvx source (default: the GitHub repo's mcp/ subdirectory;
//                   set to "isaac-mcp" once the package is published on PyPI)
const { spawn, spawnSync } = require("node:child_process");

const DEFAULT_FROM =
  "git+https://github.com/kalebcoleman/isaac-mcp.git#subdirectory=mcp";
const source = process.env.TWIN_MCP_FROM || DEFAULT_FROM;

const probe = spawnSync("uvx", ["--version"], { stdio: "ignore" });
if (probe.error || probe.status !== 0) {
  process.stderr.write(
    "isaac-mcp needs uv (provides uvx). Install it: https://docs.astral.sh/uv/getting-started/installation/\n"
  );
  process.exit(127);
}

const child = spawn("uvx", ["--from", source, "isaac-mcp", ...process.argv.slice(2)], {
  stdio: "inherit",
});
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => child.kill(sig));
child.on("exit", (code, signal) => process.exit(signal ? 1 : code ?? 0));
