# The bridge protocol

How a client talks to the bridge inside Kit. MCP clients never see this: the `isaac-mcp` server
speaks it for them. Read it to script the bridge directly or to write another client.
`mcp/isaac_mcp/client.py` is the reference client: one file, standard library only.

## Messages

A TCP connection carries one JSON object per line in each direction, UTF-8, at most 64 MiB a line.

```
request   {"id": 7, "type": "sim.stage.info", "params": {...}, "token": "..."}
reply     {"id": 7, "status": "success", "result": {...}}
          {"id": 7, "status": "error", "message": "...", "code": "READ_ONLY", ...}
```

- `type` is the tool's name: `<namespace>.<tool>`, or a bare name for the bridge's own tools.
- `id` is yours and comes back on the reply. Send one with every request: a reply that arrives
  after you gave up on its call can then never be taken for the next call's. Several requests may
  be in flight on one connection; replies come back as calls finish, not in the order sent.
- `token` is needed on the first request of a connection when the bridge requires it (see below).
- An error reply carries the message and whatever the tool attached: often a `code`, and for an
  unexpected exception its `error_type` and `traceback`.

## Finding a bridge

A bridge writes `<pid>-<port>.json` into a folder only its account can read:
`$XDG_RUNTIME_DIR/isaac-mcp`, or `/tmp/isaac-mcp-<uid>`. The file names the app, PID, port and
address, and whether the bridge is read-only or needs a token. The reference client uses, in order:
the port it was given, `UR5E_DT_PORT` or `ISAAC_MCP_PORT`, the one bridge advertised. With several
advertised and none chosen it refuses and lists them.

```bash
python3 mcp/isaac_mcp/client.py --list-bridges
python3 mcp/isaac_mcp/client.py status --port 8766
python3 mcp/isaac_mcp/client.py sim.stage.info
```

Exit codes: 0 success, 1 the bridge answered with an error, 2 unreachable or bad usage.

## The token

A bridge requires a token unless it was started with `ISAAC_MCP_AUTH=0`. At start it makes a
random one, or uses `ISAAC_MCP_TOKEN`, and writes it to `<pid>-<port>.token` beside its portfile,
readable by its own account only. A client on the same account reads that file, so nothing needs
configuring on one machine. From another machine, or another account, set `ISAAC_MCP_TOKEN` for
the client to the same value. A connection whose first request has no valid token gets
`AUTH_REQUIRED` and is closed.

The token stops other local accounts and stray network clients from running code in your
simulator. It is not encryption: over an untrusted network, use an SSH tunnel.

## When a call runs

Tools run on Kit's main loop. Three rules decide when:

- **Inline** tools touch nothing in Kit and are answered at once on the socket thread, whatever
  Kit is doing: `status`, `job_status`, `list_tools`, `list_bridges`, and the file tools
  (`put_file`, `get_file`, `stat_file`), which only use the disk. So you can always ask what is
  running, wait for a job, and move a large file, without waiting for a frame or holding one up.
- **Exclusive** tools may advance the app from inside their own call; `run_python` is one. An
  exclusive call waits for running calls to finish, runs alone, and calls that arrive meanwhile
  wait behind it.
- Every other tool starts in arrival order as soon as no exclusive call is running or waiting.

A call that needs the main loop starts on the next Kit frame, so it takes at least about one frame
(a few milliseconds headless) however little it does. A request whose connection closes before it
starts is dropped. Replies are never sent in a way that makes Kit wait for the client: a client
that stops reading is disconnected once 256 MiB of replies are waiting for it. `status` reports what is running
and how many wait (`busy`).

## Read-only mode

Started with `ISAAC_MCP_READ_ONLY=1`, a bridge runs only the tools registered as read-only and
answers every other with the code `READ_ONLY`, before the tool is called. `run_python` and
`put_file` are refused. `list_tools` marks each tool's `read_only`, and `status` and the portfile
say the bridge is read-only. This is enforced inside Kit, so it holds for every client.

## Jobs

Work that outlasts one call runs as a job: `run_python` with `background=true`, a controller run,
a trajectory replay. Starting one returns a `job_id`. `job_status` reports its state, progress and
result, and with `wait_s` returns when the job ends or the wait runs out; because it is inline, the
wait costs no simulator time. `job_cancel` stops one.

Each job also has a record on disk, `<jobs folder>/<job_id>/job.json`, in the same format as an
Isaac Lab job with `"runner": "kit"`. The host reads it when the simulator has gone, and lists
simulator and Lab jobs together. The jobs folder is `ISAACLAB_JOBS_DIR`, or
`~/.local/share/isaac-mcp/jobs`.

## Several simulators, and other machines

Give each Kit process its own port. A port that is taken makes the bridge refuse to start and say
so, rather than move to a port no client expects. Every bridge advertises itself, so
`--list-bridges` shows them all, and a client that was given no port refuses to pick between them.

The bridge listens on `localhost` only, because it runs code. From another machine the simplest
way in is an SSH tunnel to the port. To listen on a private address instead, set `ISAAC_MCP_BIND`
to that address (never `0.0.0.0` on a machine the internet can reach) and give the client the
token as `ISAAC_MCP_TOKEN`, with `ISAAC_MCP_HOST` and `ISAAC_MCP_PORT`. A remote client cannot
read the portfiles; the `list_bridges` tool, asked of any bridge it can reach, lists the others
on that machine.

Every setting is listed in [ENVIRONMENT.md](../../docs/ENVIRONMENT.md#bridge-isaacmcpbridge).
