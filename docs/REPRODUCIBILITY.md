# Reproduce an installation and a simulation result

A reproducible report identifies **source, environment, execution and observed effects**.
A lockfile or passing host suite alone does not establish native simulation behavior.
Use [Linux setup](LINUX-SETUP.md) for the everyday installation path, the
[compatibility matrix](COMPATIBILITY.md) for measured build/mode scope, and
[verification](VERIFICATION.md) for test commands and immutable historical results.

## Locked simulator setup

Use a new checkout dedicated to the selected source. The following pins the source used by
the recent clean locked native follow-ups; it is an integration commit, **not a release tag**.
It selects standard released NVIDIA packages and the repository's extensions/dependency pins.
No modified NVIDIA vendor source or hand-copied renderer stubs are required.

First satisfy the [Linux/GPU/storage prerequisites](LINUX-SETUP.md#platforms-and-prerequisites)
and install [uv](https://docs.astral.sh/uv/getting-started/installation/). Run this sequence
only for a new 6.2 environment, after reading and accepting NVIDIA's EULA:

```bash
git clone https://github.com/kalebcoleman/isaac-mcp.git isaac-mcp-repro
cd isaac-mcp-repro
git checkout --detach c2ceb7f1b8130f414872cb4475ffdd1c2d38b96a
git rev-parse HEAD
git status --short
sha256sum pyproject.toml uv.lock
export OMNI_KIT_ACCEPT_EULA=YES
uv sync --locked --python 3.12 --link-mode copy
uv pip check --python .venv/bin/python
.venv/bin/python -c 'import importlib.metadata as m, sys; print(sys.version); print({n:m.version(n) for n in ("isaacsim", "isaacsim-kernel", "torch", "torchvision", "numpy", "mcp")})'
```

`--locked` refuses an out-of-date lock instead of changing it; see
[uv's locking semantics](https://docs.astral.sh/uv/concepts/projects/sync/).
`--link-mode copy` avoids relying on cache hardlinks, but the download cache can still be used.
The exact experiment used an available Python **3.12.3** interpreter selected by absolute path;
`--python 3.12` may select another patch version, so record the interpreter actually chosen.
For exact interpreter matching, substitute the path to your chosen compatible Python 3.12.3.

Expected input hashes at this source (also identical at the setup experiment's `2fdf404`):

| Input | SHA-256 |
|---|---|
| `pyproject.toml` | `3655e3401e1b6a7dd0c8b81a01ab879ca223fadaa5fc53f137d1ba87d6e78b83` |
| `uv.lock` | `9d97f4791f939c1bd377cb9ad85060ce5720998ea3da8ca4ee281a2be280bfb3` |

This command installs **simulator dependencies only**. It does not perform the full installer's
machine checks, host installation, ffmpeg shim creation or client registration. After a successful
check, install the host from this checkout and configure the client separately:

```bash
./install.sh --no-sim
export PATH="$HOME/.local/bin:$PATH"
isaac-mcp --help
```

The host step force-replaces the user-level editable tool and may create its ffmpeg shim.
For manual host installation without a shim, use
`uv tool install --force --editable './mcp[isaaclab]'` instead.
Neither command modifies the simulator lock. The host has its **own dependency resolution**;
the root `uv.lock` does not freeze the host tool, external ROS or Lab. Record their versions
separately. The host requires MCP `>=1.22,<2`; the recorded Sim lock's MCP **1.12.4** is not the
host's SDK and is not a reason to install the host inside Sim.

Follow [client registration and read-only identity checks](LINUX-SETUP.md#connect-and-check-without-changing-the-scene),
then launch an authorized session with `./run.sh --port 8766` (or `--headless`). The launcher
uses this checkout's `.venv`; `UR5E_DT_PYTHON` explicitly selects another interpreter.
Do not run a second full installer afterward merely to register a client: that would repeat
its non-locked simulator sync. Use `--no-sim` with the chosen registration flag or manual config.

## What has actually been verified

| Evidence | Observed outcome | Limits |
|---|---|---|
| Full CPU/package checks at `d725f84cb853bf5ceb2fa3b7d13d0e002d29becf` | 1,246 host tests; 216 minimum-SDK tests; docs/privacy/assets/catalog, wheel/sdist and npm dry run passed | Intentional ZIP and Pydantic lifespan warnings retained; no native qualification from CPU checks. [Exact record](VERIFICATION.md) |
| New isolated venv, inputs from `2fdf4044ffdaa081431c14485a88d00abe185c07` | Locked sync exited 0 in **18.13 s**; `uv pip check` passed **186 packages**; metadata checked; both input hashes unchanged | Existing Ubuntu machine and populated cache. Copied project inputs, no full `install.sh`, client registration or existing environment changes |
| Native follow-ups using that locked venv at `c2ceb7f1b8130f414872cb4475ffdd1c2d38b96a` | Committed records cover both PhysX modes, independent actual use and fresh reopening of the older agent save | Separate from the install-only record. The newer clean-agent save also passed a separate later 16-call / 12-check reopen; see the [three-save follow-up](COMPATIBILITY.md#three-later-agent-save-reopens). No full-matrix or warning-free claim |
| Existing environments used for earlier 6.2 demo runs | Completed effects retain their original source and installed stack | NumPy **2.5.4** conflicts with the installed kernel's **2.3.1** pin; not dependency-clean. No causal warning/stall inference |
| Previously prepared 6.0/6.1 environments | Four version-specific dependency checks passed | Not clean installs from this repository's 6.2 lock; mode/source-specific native results remain separate |
| Lab 3.0 / Sim 6.2 video checks | Scoped train/eval/play results recorded | Existing Lab environment with a private dependency overlay; not clean Lab/video installation qualification |

The inspected clean windowed native review at exact `c2ceb7f` has SHA-256
`0a1de1d85db1c9652f3c83d15af607e047083aeca6a627c4052eed32cb63c3d3`.
Its **35,378,299-byte log prefix** contains **24 error markers, 83,206 warning markers**
and **82,297 simulation-time warning occurrences**; cycle/reentry/GetCamera/force-callback/
edit-target markers were zero. The sampled Console showed **60,703 warnings / 63 errors**,
a different cumulative scope. These are retained observations, not a harmlessness claim.
The review sampled two of 193 decoded diagnostic-video frames, with cropping; it does not
establish that every frame was inspected. This parent review precedes the separate independent
clean-stack task. The [committed parent/setup index](evidence/project-scene/RECENT-62-20261102.json)
and [independent-task addendum](evidence/project-scene/RECENT-62-AGENT-20261102.json) now record
51 and 15 source hashes respectively. The independent task's 92 requests / 17 checks and
bounded eight-warning / zero-error interval are separate from the final whole-session
24-error / 83,214-warning log. The successful reopen in the parent index is the older
`2fdf404` output. A [later committed supplement](evidence/project-scene/RECENT-6001-20261102.json)
records the newer clean-agent save's own fresh headless reopen at exact `c2ceb7f`:
16 named calls, 12 passed checks, unchanged source, both articulations, supported sample
and a new live camera. Twelve steps advanced approximately 0.1 simulation seconds at
120 Hz; the final native log had 17 warnings and zero errors, followed by normal shutdown.
Two runtime-owner-reviewed images have cropped UR5e framing. This validates composed USD
and new runtime initialization, not a solver/controller checkpoint. Original task ledgers
remain unchanged; neither reopen closes the original 6.0.1 stall or remaining matrix gates.

The install-only record contains the actual commands, return codes and package metadata;
its original scope is not rewritten by later native work. Checked packages included:

| Package | Locked venv observation |
|---|---|
| Python / Isaac Sim | 3.12.3 / 6.2.0.0 |
| Torch / Torchvision | 2.11.0+cu130 / 0.26.0+cu130 |
| NumPy | 2.3.1 |
| Sim-environment MCP | 1.12.4 |
| Newton / MuJoCo / MuJoCo Warp | 1.6.1 / 3.12.0 / 3.12.0 |

Installed backend packages do not establish that their workflows were exercised. Generic
Newton qualification and an Isaac Lab Newton/MJWarp pairing need their own evidence.
The test platform was Ubuntu **24.04.5**, x86_64, RTX **5080**, driver **580.178.04**;
windowed observations used **X11**. Other GPUs, drivers, display servers and OS combinations
require separate checks against [NVIDIA's requirements](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/requirements.html).

Budget for download size, shader compilation and startup. The runtime owner recorded about
**222 seconds** for the first locked-environment shader/startup sequence; the 18.13-second
cached sync above is not a clean-machine installation estimate. Monitor startup logs and
bridge readiness rather than treating a quiet window or a fixed timeout as success.

**Still open:** clean-machine and full-installer execution; clean Lab video dependency setup;
remaining affected older-build checks, generic backend qualification and final matrix review;
the original 6.0.1 windowed native-step stall. Later passing runs do not erase that stall.
Draft evidence attachments remain unpublished; historical invocation of all 99 tool names
is not verification of all tools on all configurations.

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
SIM_PYTHON="${UR5E_DT_PYTHON:-$PWD/.venv/bin/python}"
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
