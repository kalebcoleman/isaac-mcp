# Plan 03: `gap` and `vabar` wrappers

Roadmap [Phase 6](../ROADMAP.md#phase-6-planning-and-evaluation). The pull request needs no GPU: every test uses a fake
checkout. Using the tools afterwards does. Start after plan 02, so the tools land in the `planning` and `eval` groups.

## Problem

Graph-as-Policy (GaP) compiles a task into a typed skill graph and improves it in simulation; VABAR benchmarks such graphs on
Newton through RoboSimStudio. Both are Apache-2.0, young, and ship a command line and no MCP server. They must be usable from
twinsmith without their dependencies entering it, and GaP must never be pointed at a robot.

Checked upstream on 2026-11-13:

| | Graph-as-Policy | VABAR |
|---|---|---|
| Entry point | `gap` (`gap.cli:main`); `gap run --sim SUITE/TASK` | `python -m vabar list`, `run`, `bench`, `studio`, `warmup` |
| Real hardware | `gap run --real franka` or `--real ur_zed` exists | Not looked for; read `python -m vabar run --help` at the pin and refuse any such option the same way |
| Needs | 24 GB of GPU memory, `HF_TOKEN`, an OpenRouter or Vertex key | A one-time `warmup` of 5 to 8 minutes |
| Pins | VABAR's README names forks: GaP `06dd6193e13eab816123febef1369deda0849d91`, skill library `b15859a244658abc6060f3aa3b6827780746a5fe` | Its 1.0 release |

## Design

Follow the [wrapper contract](../ARCHITECTURE.md#wrapped-native-beside), which `mcp/twinsmith/robosim/` already implements.
Take its shared parts out into one small module first, then write two thin wrappers on it.

### `mcp/twinsmith/sibling.py` (new, extracted from `robosim/bridge.py`)

- `Checkout(name, env_var, pin)`: resolves the root (`TWINSMITH_<NAME>_ROOT`, else `<workspace>/tools/<name>`), reads the
  commit, finds `.venv/bin/python`, and returns `{checkout, commit, pin, pin_match, environment_present, warnings}`.
- `run(argv, *, cwd, env_pass, timeout_s)`: a bounded subprocess with a scrubbed environment (only `PATH`, `HOME`, GPU
  variables and the names in `env_pass`), captured tails, and an exit code.
- `start_job(argv, ...)`: the same as a job record in the shared jobs folder, so `jobs` follows and cancels it.
- `robosim/bridge.py` then uses these instead of its own copies. Its tests must pass unchanged.

Result envelope for both tools: `{ok, tool, action, commit, pin, pin_match, exit_code, result | error, warnings}`.

### `gap` (toolset `planning`)

| Action | Runs | Notes |
|---|---|---|
| `status` | Nothing upstream | Checkout, pin, GPU memory against the 24 GB requirement, which of `HF_TOKEN`, `OPENROUTER_API_KEY` are set (never their values), the skill-library checkout |
| `tools` | `gap tools list --format json` | Read-only |
| `check` | `gap check <graph>` | Read-only; `graph` must be inside the run folder or an approved root |
| `generate` | `gap generate ...` | Calls a paid model: refused unless `[planning] allow_paid = true` in the owner's configuration |
| `run` | `gap run --sim <suite/task> ...` | A job. GPU guard as `isaaclab__launch` (`allow_concurrent_gpu`) |

Only `run` with `--sim` and `--real` was read in the upstream source. The other subcommand spellings come from a survey:
confirm each with `gap <subcommand> --help` at the pin before wiring it, and leave out any that differ rather than guess.

**The safety rule.** The wrapper has no argument that produces `--real`. `sim` is required for `run` and must match
`^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`. There is no `extra_args`. As a second line, the built argument list is scanned before
the process starts, and any element equal to `--real` or starting with `--real=` raises `REAL_ROBOT_REFUSED` with the
message "twinsmith does not run Graph-as-Policy on real hardware". The subprocess environment carries no `ROS_DOMAIN_ID`.

### `vabar` (toolset `eval`)

| Action | Runs | Notes |
|---|---|---|
| `status` | Nothing upstream | Checkout, pin, the pins of the GaP and skill checkouts beside it, whether `warmup` has run |
| `list` | `python -m vabar list` | Read-only |
| `run` | `python -m vabar run --task <t> --solve <routine or graph> [--episodes ...]` | A job |
| `bench` | `python -m vabar bench --cell <c>` | A job. `print_only=true` (the default) returns the plan without running; `workers` is 1 to 4 |
| `results` | Nothing upstream | Reads the run folder's result files and returns the summary table |

`warmup` and `studio` are not wrapped in this pull request: `warmup` is a one-time setup the owner runs, and `studio` opens
a network viewer.

Checkout layout, on the ASU PC, all the owner's to create. Graph-as-Policy is not set up on the home PC: its 16 GB GPU is
below the 24 GB requirement, and `gap(action="status")` reports that shortfall rather than working around it.

```text
<workspace>/tools/
  graph-as-policy/      at VABAR's pin; serves gap and vabar
  open-robot-skills/    at VABAR's pin
  vabar/
```

## Files

| File | Change |
|---|---|
| `mcp/twinsmith/sibling.py` (new) | As above, about 150 lines |
| `mcp/twinsmith/robosim/bridge.py` | Use `sibling`; delete the code it replaces |
| `mcp/twinsmith/gap.py`, `mcp/twinsmith/vabar.py` (new) | `PIN`, argument building, the registration function. One file each, about 200 lines |
| `mcp/twinsmith/toolsets.py` | `planning` and `eval` toolsets, off; `_ROOTS` entries `gap`, `vabar` |
| `mcp/twinsmith/server.py` | Register in `_register_host_tools` when the toolset is on |
| `mcp/twinsmith/config.py` | `[planning] allow_paid` |
| `mcp/tests/test_sibling.py`, `test_gap_wrapper.py`, `test_vabar_wrapper.py` (new) | Below |
| `docs/PLANNING-EVAL.md` (new), `docs/README.md`, `docs/TOOLSETS.md`, `docs/ENVIRONMENT.md`, `README.md` (generated) | One page for both tools: setup steps the owner runs, the actions, the safety rule |
| `docs/ARCHITECTURE.md`, `docs/ROADMAP.md` | Move both tools from proposed to existing |

Nothing from either upstream repository is copied, so `THIRD-PARTY-NOTICES.md` does not change.

## Tests

A fake checkout is a temporary git repository with a `.venv/bin/python` shell script that prints its arguments as JSON.

- `Checkout` reports a pin match, a mismatch with a warning, a missing checkout with setup guidance, and never runs an
  installer.
- `gap run` builds exactly `["<checkout>/.venv/bin/gap", "run", "--sim", "libero/0", ...]`; `vabar` builds
  `["<checkout>/.venv/bin/python", "-m", "vabar", ...]`.
- **Refusal:** `sim="x --real franka"`, `sim="--real=ur_zed"`, a `graph` path named `--real`, and a monkeypatched builder
  that injects `--real` each end in `REAL_ROBOT_REFUSED`, and the fake interpreter is never started.
- The function signature of `gap` has no parameter named `real`, `extra_args` or `args`.
- `generate` is refused without `allow_paid`; `status` output contains `"HF_TOKEN": true` or `false` and never the value.
- The subprocess environment has no `ROS_DOMAIN_ID` even when the server's has one.
- `vabar bench` with defaults starts no process and returns the planned command; `workers=9` is refused.
- With `planning` and `eval` off, neither tool is registered; each group alone lists exactly one tool.

## Acceptance checks

1. The CI commands in the [plans index](README.md) pass, and the existing `test_robosim_bridge.py` passes unchanged.
2. On the ASU PC, once the checkouts exist: `gap(action="status")` and `vabar(action="status")` report the pins;
   `vabar(action="list")` returns the task list.
3. Recorded later, not required to merge: one VABAR cell run from the tool on the ASU PC, scheduled around Jenga games, its
   result beside the published number.

## Not in this pull request

`agentic-self-learning` (it drives another paid agent on its own), VABAR's Isaac Lab Arena backend, and a Jenga skill graph.
