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
uses this checkout's `.venv`; `ISAAC_MCP_PYTHON` explicitly selects another interpreter.
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
| Historical Lab 3.0 / Sim 6.2 video checks | Scoped train/eval/play results recorded | Existing environment with a private dependency overlay; historical setup unchanged |
| Later self-contained Lab 3.0 / Sim 6.2 environment at `c2ceb7f` | All 12 tools, training/artifacts and sampled video/mode checks | No inherited `.pth`; two dependency conflicts remain. See [inputs, commands and limits](#self-contained-lab-3-reproduction) |

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

**Still open:** clean-machine and full-installer execution; dependency-clean Lab setup;
remaining affected older-build checks, generic backend qualification and final matrix review;
the original 6.0.1 windowed native-step stall. Later passing runs do not erase that stall.
Draft evidence attachments remain unpublished; historical invocation of all 99 tool names
is not verification of all tools on all configurations.

## Self-contained Lab 3 reproduction

The [committed Lab record](evidence/project-scene/RECENT-LAB-20261102.json), SHA-256
`62d4f86037026cedb8bf576f69baff902121b3af6102c7579822ba45e1a62273`, records MCP source
`c2ceb7f1b8130f414872cb4475ffdd1c2d38b96a` and official Lab source
`e3f7d9ff1f5d7f8280de3ece9000b7117f66bcf4`. All 91 original record hashes and sizes were
verified for this documentation. This used **Lab 3.0 / Sim 6.2.0.0 / Newton MJWarp** on
Ubuntu 24.04.5, RTX 5080, driver 580.178.04 and Python 3.12. Standard vendor packages
and the pinned official wheel-builder supplied the runtime; matching upstream scripts
supplied job entry points. No inherited `.pth` overlay or existing user environment was changed.

The 12-tool workflow made **111 calls plus three cleanup calls (114 retained), with
16 assertions**. A separate 94-call mode run covered windowed training, headless eval/play
videos and windowed play; 25 desktop-review calls and seven final status calls are separately
recorded. Real two-iteration training produced checkpoints and metrics. All seven owned
jobs exited. One cancelled job's cross-host final exit code was unavailable in metadata;
named cancellation and subsequent `pid_alive=false` were verified. This tests workflow
execution, not learned-policy quality or long-run stability.

Three videos decoded 60 frames each at 1280×720. Runtime-owner review sampled frames
0/30/59 of each: windowed evaluation frame 0 was black; later samples and headless samples
showed moving Cartpoles. Cold shader compilation took approximately 107 seconds, with
app readiness at 180.9 seconds in that run, not a general startup bound. The final actual
Console sample had **25 warnings / zero errors**; retained logs also contain protobuf
`health.proto` E/W messages, IFactory and MoviePy warnings. This is not a warning-free or
whole-log zero-error claim. Repeated-character input worked in this Lab window; earlier
Isaac Sim desktop partials remain unchanged. No new pixel review was performed for this guide.

### Dependency outcome and exact inputs

The new environment resolved **284 packages**, but `uv pip check` exited **1**:

| Requirement | Installed value | Retained conflict |
|---|---|---|
| Sim core: `fsspec==2026.2.0` | `2026.9.0` | Lab/Torch requires a newer floor |
| Sim kernel: `urllib3==2.7.0` | `2.8.0` | Lab requires the newer security floor |

The pinned upstream Lab configuration explicitly overrides these constraints. Native
passes do not remove the metadata conflicts. Do not downgrade merely to turn the check
green or describe this as dependency-clean. This was a new environment on an existing
machine/cache, not a clean-machine or full `install.sh` test. Lab Newton MJWarp does not
qualify generic Sim Newton tools or the UR twin on Newton; the original `20f2953` Sim
stall also remains unresolved.

The [draft-only reproduction bundle](https://github.com/kalebcoleman/isaac-mcp/releases/download/untagged-8a38d708b61458020a38/lab3-sim62-c2ceb-selfcontained-redacted.tar.gz)
is **not publicly available while the release remains draft**. Authorized reviewers can
use its `setup` directory. The [release index](evidence/project-scene/RELEASE-ASSETS.md)
records the artifact; its size is 1,505,566 bytes and SHA-256 is
`c417aee1540d61f6a44aab8ec9a02dc921ff60021b8e133751bfad5f6ce24022`.

| Bundle member | SHA-256 |
|---|---|
| `setup/pyproject.toml` | `3b651e833878d71e90d83280a75cbed4a50573af63801cecc543b4cac99f9544` |
| `setup/uv.lock` | `9696c5ec756074be1ae05271c678b19d5296b5468cf997ffb181d916bf5356e2` |
| `setup/source-root-manifest.json` | `482a181d187e662eb3e203d269eb192580a27e757f8a152024c9051a7f8fb874` |

Use these inputs together. The profile pins the official Lab wheel-builder and rl-games
source revisions, CUDA 13 Torch index, NumPy 2.3.1 and MoviePy 1.x. It retains explicit
resolver overrides and `unsafe-best-match` selection across the configured package indexes.
It is an evidence-specific profile, not the root Sim lock or a generic `pip install isaaclab`.
See [prerequisites and NVIDIA licenses/EULA](LINUX-SETUP.md#platforms-and-prerequisites) before installation.

### Reproduction command scope

Run from the MCP checkout root. Replace the absolute paths with user-selected locations.
`lab_profile_dir` is the reviewed bundle's extracted `setup` directory; `lab_source_dir`
is an official Lab Git checkout containing the pinned commit. The destination must be new.
These parameterized commands were inspected and syntax-checked, **not executed end to end**
by the documentation owner. Stop on any failure; never reuse an existing user environment.

```bash
lab_profile_dir=/absolute/path/to/reviewed-bundle/setup
lab_source_dir=/absolute/path/to/official-IsaacLab-checkout
lab_repro_dir=/absolute/path/to/new-lab-reproduction
lab_python=/absolute/path/to/python3.12
lab_revision=e3f7d9ff1f5d7f8280de3ece9000b7117f66bcf4
(
  set -e
  mkdir -m 700 "$lab_repro_dir"
  cp "$lab_profile_dir/pyproject.toml" "$lab_profile_dir/uv.lock" "$lab_repro_dir/"
  cd "$lab_repro_dir"
  sha256sum --check <<'HASHES'
3b651e833878d71e90d83280a75cbed4a50573af63801cecc543b4cac99f9544  pyproject.toml
9696c5ec756074be1ae05271c678b19d5296b5468cf997ffb181d916bf5356e2  uv.lock
HASHES
)
```

The **measured preparation** used this command shape, without `--locked`:

```bash
uv sync --project "$lab_repro_dir" --python "$lab_python" --link-mode copy
```

For a proposed replay of the retained lock, use the following **instead**. That exact
locked replay has not been independently executed; the earlier unlocked sync does not
verify it. Record command output/exit status and verify input hashes afterward.

```bash
uv sync --locked --project "$lab_repro_dir" --python "$lab_python" --link-mode copy
uv pip check --python "$lab_repro_dir/.venv/bin/python"
```

The retained package check exited 1 with the two conflicts above; preserve that outcome
and investigate any additional conflicts. Inspect versions without importing Kit/CUDA:

```bash
"$lab_repro_dir/.venv/bin/python" -c 'import importlib.metadata as m; print({p: m.version(p) for p in ("isaaclab", "isaacsim", "torch", "numpy", "moviepy", "imageio-ffmpeg", "fsspec", "urllib3")})'
```

Installed wheels provide runtime/task modules, but job launch also needs the matching
scripts. The measured setup extracted these from the clean pinned official source.
This parameterized extraction preserves that distinction:

```bash
(
  set -e -o pipefail
  mkdir "$lab_repro_dir/IsaacLab"
  git -C "$lab_source_dir" archive "$lab_revision" \
    isaaclab.sh VERSION README.md LICENSE scripts/reinforcement_learning \
    | tar -x -C "$lab_repro_dir/IsaacLab"
)
export ISAACLAB_PYTHON="$lab_repro_dir/.venv/bin/python"
export ISAACLAB_ROOT="$lab_repro_dir/IsaacLab"
```

Check the extracted files against `setup/source-root-manifest.json`. This is not a complete
editable Lab source installation. Put those interpreter/root values in the **MCP host/client
environment**, then restart that connection; exporting them in an unrelated terminal does
not reconfigure an already-running host. Call `isaaclab__install_info` to confirm selection.
Host TensorBoard support remains a separate dependency. Coordinate exclusive GPU ownership
before launching jobs and use the [Lab tool workflow](TOOLS-ISAACLAB.md#workflows)
for task arguments, status, checkpoints and cancellation. A job ID alone is not completion.

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
