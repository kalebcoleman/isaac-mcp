# Reproduce an installation or result

A useful report identifies **source, environment, execution and observed effects**. A lockfile
or a passing host suite alone does not establish native simulation behavior.

For a new setup, start with [Linux setup](LINUX-SETUP.md); use the
[compatibility matrix](COMPATIBILITY.md) for measured build and backend scope and
[verification](VERIFICATION.md) for checks and their effects. The host, Sim, Lab and external
ROS can have separate environments, so record their identities separately.

## Earlier experiments

Past test ledgers and exact historical reproduction scripts are available in
[Git history](https://github.com/kalebcoleman/isaac-mcp/tree/c66ae924bb6fe43387a2ab54f458d394bffb7255/docs). They targeted earlier source revisions and are not a recipe
for installing the current project. For a new installation, use the setup guide above.

## Record your own run

Keep logs and generated scenes outside Git. From the checkout root, these read-only commands
record source and simulator package identity without importing Kit or running a CUDA kernel:

```bash
git rev-parse HEAD
git rev-parse HEAD^{tree}
git status --short
sha256sum pyproject.toml uv.lock
uv --version
uname -srmo
cat /etc/os-release
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
printf 'DISPLAY=%s XDG_SESSION_TYPE=%s\n' "${DISPLAY:-unset}" "${XDG_SESSION_TYPE:-unset}"
SIM_PYTHON="${ISAAC_MCP_PYTHON:-$PWD/.venv/bin/python}"
"$SIM_PYTHON" -c 'import importlib.metadata as m, sys; print(sys.executable); print(sys.version); print({n:m.version(n) for n in ("isaacsim", "isaacsim-kernel", "numpy", "torch", "mcp")})'
uv pip check --python "$SIM_PYTHON"
uv tool list
```

For the host, retain `uv pip freeze --python <host-python>` and `uv pip check --python <host-python>`
using the interpreter of the installed uv tool (located under `uv tool dir`). Record Lab and
external ROS in their own interpreters/terminals. Do not use a system ROS Python environment
to stand in for Sim's bundled bridge.

| Record | Include |
|---|---|
| Source | Full commit/tree, dirty diff, input hashes, any diagnostic launcher deviation |
| Setup | Exact commands, tool/interpreter versions, exit codes, cache use and fresh versus existing environment |
| Runtime | Sim package and app/Kit build, backend, headless/windowed, GPU/driver/display, selected PID/port/session/stage |
| Effects | Requested operation, tool result, independent state/pixel/physics observations, source preservation |
| Outcome | Attempted/pass/fail/pending/unsupported; warning/error counts with the precise log interval |
| Media | Original hashes, decoded frame count versus visually sampled frames, redaction and asset notices |
| Recovery | Response/timeout, subsequent named health checks and observed process exit; cleanup is not recovery |

A timeout or client disconnect does not prove native cancellation. A successful import does
not prove physics readiness. A successful save is not a solver checkpoint: reopen in a fresh
process and verify the properties and effects you need. The legacy exporter is a root-reference
copy plus eligible local PhysicsScene settings, not a full session snapshot; see [save contracts](TOOLS-SCENE.md).

Before sharing evidence, replace personal paths with neutral placeholders and visually inspect
screenshots. Preserve original private records; publish a separate redacted summary with hashes.
Keep USD/user assets, large logs and videos out of Git. See [media publication](media/README.md#video-access-and-publication)
and [asset notices](provenance/ASSETS.md).
