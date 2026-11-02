# Verification

The automated host suite and live simulator checks cover different things. A passing host suite does not establish
that Isaac Sim, the renderer, ROS, or a particular robot work on a machine. Record the commit, machine, driver,
versions, commands, and any warnings when reporting a result.

For installation identities, lock-preserving commands and evidence-reporting fields, use the
[reproducibility guide](REPRODUCIBILITY.md). For a non-destructive first connection, use the
[setup check](LINUX-SETUP.md#connect-and-check-without-changing-the-scene). The suites below have
different scopes; native verifiers can build scenes and move joints.

## Finding per-tool evidence

The [current tool/source/backend reconciliation](evidence/project-scene/CURRENT-TOOL-RECONCILIATION.md)
is the entry point for all 99 tool rows, exact discovery profiles, source comparisons and
remaining gates. Its [JSON projection](evidence/project-scene/CURRENT-TOOL-RECONCILIATION-20261102.json)
has SHA-256 `07bb8d57b4bc61f2882a7b72563ea2f7347036fb6856608760c045ee3d104808`.
The 12,073 indexed call records and 1,583 observation links include repeated observations,
expected refusals and failed workflows: they are not unique dispatches, successful effects
or an all-99 qualification. Use each row's source/build/backend/mode and bounded observation;
matching module bytes do not turn historical calls into a new complete-head execution.

The real-MCP inventory belongs to `fdb7a02da1be003e3d338f676ba8105720869ea3`.
Keep its default and optional-inclusive profiles separate; the measured optional-inclusive
scene-off counts do not replace default-group guidance. The ledger explains its `O`, `C`,
`F` and absent cells and links the unchanged historical index plus draft-access records.

The [current PhysX positive-path supplement](evidence/project-scene/PHYSX-CURRENT-POSITIVE.md)
closes the reconciliation's then-open normal `sim__body__state`, `sim__joints__state` and
`sim__simulation__step` gap across five builds in both modes, on exact
`0163b3f07e6b9d3f51006f8845156f664429e580`. Its
[compact record](evidence/project-scene/RECENT-PHYSX-POSITIVE-0163-20261102.json) has SHA-256
`6fbc9cce10e1feab8a89712ac7afd4cc84d85e2b02e696e1bae363021711c549`;
all 67 key original record hashes and sizes were verified for this documentation update.
The immutable 99-tool JSON above is unchanged; follow the supplement for later evidence.

The **445 core calls include 100 expected errors**, with **230 assertions** covering targeting,
refusals, finite state, measured effects/timing, reinitialization and source-file hashes.
Successful requests advanced 3,250 steps at 120 Hz in total, not 3,250 rendered frames or a
wall-time bound. The first 6.2 headless reader failed before Play; its corrected test recreated
the stopped stage in the same process. Other qualified cases used fresh processes. Launch and
desktop-schema refusals remain recorded, with no weakened guards. All ten owned PIDs exited
normally and were confirmed absent. Five windowed Console reviews had zero errors and nonzero
warnings; their counters are separate from final native logs. This update did not repeat pixel
review, native execution or suites.

Prepared older environments and the cached locked 6.2 setup with diagnostic launchers are
not a clean-machine/full-installer test. Source-file preservation is not whole-root equality.
This smaller fixture does not reproduce or resolve the original `20f2953` stall. Fresh-Kit
camera reuse, installation/Lab limits and final review remain separate; Newton limitations
remain experimental. Neither this targeted pass nor CPU tests certify all 99 tools on the
current head or declare the candidate merge-ready.

## Interpreting deliberate asset failures

Native regression harnesses may temporarily hide a robot asset with offline mode enabled,
then supply a corrupt cached file, to verify explicit missing/corrupt-asset refusals. Those
calls intentionally fail and can leave Error entries in the Console. A passing **test** means
its expected refusal was observed; it does not mean the failed Quick Start loaded a robot.
The separate recovery check must restore the original bytes, verify their hash and demonstrate
successful named-tool loading. Retain the failed calls and recovery records together.

For example, the retained `current6000w_c2ceb-asset-failures` summary at exact
`c2ceb7f1b8130f414872cb4475ffdd1c2d38b96a`, Sim **6.0.0.0 windowed PhysX**, records
14 calls and four checks: missing optional bench/placeholder, missing offline robot refusal,
corrupt robot refusal, and restored-cache recovery. The restored robot SHA-256 is
`d7a290e94f3de151fdf660007f6a53448cbda8cf558d83fd04a91c00eda5bbc7`, matching the manifest.
This disposable-scene test is not proof that Quick Start transactionally preserves an arbitrary
existing stage, and it does not complete the version/mode matrix.

Outside an identified negative test, an offline-missing or corrupt-asset error is an actual
setup/operation failure. Inspect the selected cache and follow [asset preparation](LINUX-SETUP.md#prepare-the-ur5e-and-sensor-assets);
do not label an unexplained error expected merely because similar tests exist. Keep cumulative
Console counts distinct from scoped native-log intervals and do not suppress unrelated errors.

## Host checks

From the repo root, run:

```bash
cd mcp
uv run --isolated --python 3.12 --with-editable . --extra test --extra isaaclab --extra scene-test pytest -q
uv build
```

From the repository root, `python3 scripts/check_docs.py` checks relative file links, fenced JSON examples and the
registered tool names in the four tool references, agent tier counts, skill tool names, and private-path bytes. It does not check external URLs, link fragments, parameter
semantics, or every prose claim.

The `scene-test` extra installs standalone `usd-core` into the isolated **host test** environment.
Do not add it to a Sim environment as a substitute for Kit's USD bindings. From the repository root,
`python3 scripts/check_assets.py` checks bundled asset licences and
`python3 scripts/gen_host_catalog.py --check` detects packaged twin-catalog drift.

The GitHub Actions workflow in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs these CPU-only checks
plus the documentation check and `npm pack --dry-run` on pushes and pull requests. It does not launch Isaac Sim, train a policy, or test real
hardware. A workflow is only verified on GitHub after its run actually completes.

The first completed run for commit `66e84b4` passed on 2026-10-30: 421 tests, documentation checks, wheel and sdist
builds, and the npm package dry run. [View the run](https://github.com/kalebcoleman/isaac-mcp/actions/runs/41170715313).
On the same date, the shared-bridge implementation in commit `382d2cb` passed 481 local host tests, the documentation
checks, the wheel and sdist builds, and the npm package dry run.

### Combined-candidate CPU checks (2026-11-01)

At exact commit `4499bf70c5f06c37cdf928dd1f3d2c29e5abf621` (tree
`072f8e689d3b5275c1be134488fa46b8a1c474d1`), the local full host suite passed
**1,182 tests in 170.46 seconds**, with two warnings. One was the intentional duplicate
ZIP member fixture. The other was a `PytestUnhandledThreadExceptionWarning` in
`test_bridge_admission::test_run_python_is_exclusive_and_a_mark_survives_a_wrapper`:
thread `isaac.mcp.bridge-accept` raised `OSError: [Errno 9] Bad file descriptor` at
`bridge.py:956`, in `self._socket.settimeout(1.0)`. No tests failed, but the thread
exception remains a follow-up item; this result does not establish that it is harmless.

The separate minimum-SDK run with **MCP 1.22.0** passed **216 tests in 27.74 seconds**.
It retained the Pydantic `IncompleteFieldDefinitionWarning` for an unresolved
`lifespan` forward reference. Neither warning was suppressed for these checks.

These results come from the retained local host/minimum-SDK logs and source-identity
summary. They do not establish native simulator behavior, clean installation, package
publication or completion of the version/mode matrix. The earlier **1,170 host / 216
minimum-SDK** passes and wheel/sdist/npm dry-run results belong to
`2bbeee4a1eb8ab806be44ca7120cdf599dd923e8`; packaging was not rerun as part of this
`4499bf7` check. Historical runtime failures and later scoped retests retain their own
source identities in the [compatibility record](COMPATIBILITY.md).

### CPU and package follow-up after the listener fix

At exact commit `ece55d130020e56e2f7f7781e0df6335d816e159` (tree
`10e15bffc9d7e97ddc264bdf99a715dcae7f61ec`), the local host suite passed
**1,184 tests in 170.61 seconds**, with only the intentional duplicate-ZIP-member
warning. The earlier bridge accept-thread EBADF warning was not observed in this run.
This candidate includes the listener lifecycle fix; non-recurrence in one full run
does not prove the absence of every startup/shutdown race. The `4499bf7` warning above
remains part of the historical record.

MCP **1.22.0** minimum-SDK checks passed **216 tests in 27.69 seconds**, retaining the
Pydantic unresolved `lifespan` forward-reference warning. Documentation/privacy,
asset, catalog and whitespace checks, wheel/sdist builds and `npm pack --dry-run`
also passed on this exact commit. The retained summary's SHA-256 values matched all
five local logs (host, minimum-SDK, static checks, build and npm dry run) when inspected.

These are CPU/package results, not a new native run, package publication or clean-install
qualification. In particular, the ongoing 6.0.0.1 runtime evidence at `4499bf7` is not
relabeled to this candidate. The earlier `2bbeee4` package results remain valid historical
observations; this follow-up records a separate package check at `ece55d1`.

### CPU and package follow-up after viewport and URDF fixes (2026-11-02)

At exact commit `742560d5f19b5b5dceb0a24405f6b8e26cdf3815` (tree
`4c4f5a40db67b53e830c4e11055c987735137afd`), the local full host suite passed
**1,215 tests in 170.99 seconds**, with only the intentional duplicate-ZIP-member
warning. No bridge accept-thread EBADF warning was observed in this run; the earlier
`4499bf7` warning remains historical evidence, and non-recurrence does not prove all
listener races are absent. Minimum-SDK checks with **MCP 1.22.0** passed **216 tests in
27.48 seconds**, retaining the Pydantic unresolved `lifespan` forward-reference warning.

Documentation/privacy, asset, catalog and whitespace checks, wheel/sdist builds and
`npm pack --dry-run` also passed. The host suite used the isolated Python 3.12 command
above; the separate minimum-SDK run pinned MCP 1.22.0. The retained command record
identifies uv 0.12.14, Node v26.13.0 and npm 11.20.0. Inspection confirmed the endings
and SHA-256 hashes of all five retained logs against the exact-source summary. The
static-check log is explicitly a transcription of tool output and recorded zero exits;
the host, minimum-SDK, build and npm logs retain their command output. The summary also
records wheel/sdist sizes and digests; these artifacts were built, not published.

These checks cover CPU behavior and packaging only. They do not qualify a clean native
installation, Kit/Lab/ROS execution or the remaining version/mode matrix. Native results
retain their measured source identities, including the separate `20f2953` windowed run.
Later documentation-only integration does not relabel these CPU results, and the earlier
`ece55d1` checks remain a separate historical record.

### CPU and package follow-up after checkpoint and discovery fixes (2026-11-02)

At exact commit `d725f84cb853bf5ceb2fa3b7d13d0e002d29becf` (tree
`cd67cf8705f25a3e870c5cf063a8bc8149fee6d5`), the full host suite passed **1,246 tests
in 169.89 seconds**, with only the intentional duplicate-ZIP-member warning. The
separate **MCP 1.22.0** minimum-SDK run passed **216 tests in 27.38 seconds**, retaining
the Pydantic unresolved `lifespan` forward-reference warning. All jobs completed
normally with exit zero; the checkout stayed clean at the recorded identity.

Documentation/privacy, asset, catalog, verifier shell-syntax and whitespace checks,
wheel/sdist builds and `npm pack --dry-run` passed. All five logs are captured command
output, including the static-check log; the earlier `742560d` static transcription
remains separately identified above. Inspection confirmed each log's ending and SHA-256
against the retained summary, whose SHA-256 is
`1204a87cf6b8664cdccdbf30ebaa41dea1b2fec6ebe0a84688af5cceeeaffeb8`.
The identity/summary records commands and versions: Python 3.12 test selection,
uv 0.12.14, Node v26.13.0 and npm 11.20.0. The summary records the 118,652-byte wheel
and 281,464-byte sdist with their digests; successful builds do not mean publication.

This is a new CPU/package record, not a native simulator retest or clean-install
qualification. Native observations retain their own source/build/mode identities.
These passes do not resolve the earlier `20f2953` stepping stall, establish a cause
for a concurrent native driver refusal, or qualify a pending native retry. Earlier
full-suite records and warnings remain historical evidence.

### CPU and package follow-up after Newton twin changes (2026-11-02)

At exact commit `166e10db31cc2e24fad66b204c00301c1281bfae` (tree
`914965bbe1de76e5be9bac8862579d9ac80c6069`), the full host suite passed **1,335 tests
in 171.55 seconds**, with only the intentional duplicate-ZIP-member warning. Focused
world/twin-physics compatibility and host-catalog checks passed **172 tests in 23.56
seconds**. The separate **MCP 1.22.0** minimum-SDK run passed **216 tests in 26.51
seconds**, retaining the Pydantic unresolved `lifespan` annotation warning. These are
pytest-reported durations; the records separately retain wrapper elapsed times.
All jobs completed normally with exit zero and a clean checkout at the recorded identity.

Documentation/privacy, asset, catalog, verifier shell-syntax and whitespace checks,
wheel/sdist builds and `npm pack --dry-run` also passed. Inspection confirmed the
endings and SHA-256 hashes of all six actual-command-output logs (focused, host,
minimum-SDK, static, build and npm) against the retained summary, whose SHA-256 is
`359b67e4314b5891dec0ecd25323d5f4e4882eca8651dccb4b6f4b55938139fb`.
The identity and command records retain Python 3.12.3, uv 0.12.14, Node v26.13.0,
npm 11.20.0 and the commands used. The identity record lists the 118,792-byte wheel and
288,004-byte sdist with their digests; neither artifact was published by these checks.

The integrated runtime modules match the private Newton candidate10 module hashes;
matching bytes are not a new native execution. Candidate9 broad runs and candidate10
narrow reopen/force checks retain their own identities, and exact integrated native
qualification remained pending at this CPU handoff. These results do not qualify
other Newton builds, resolve the original `20f2953` stepping stall, or establish a
clean-machine/full-installer result. Earlier CPU records and warnings remain intact;
see the [compatibility matrix](COMPATIBILITY.md) for separately measured native scopes.

### CPU and package follow-up after common wrench-header correction (2026-11-02)

At exact commit `2c8ce63b33dbabf02b2216f9d512ff9a23724d9c` (tree
`77fc20b42b97eebdb0aa04273bb7ef5281bd3001`), the full host suite passed **1,340 tests
in 170.94 seconds**, retaining the intentional duplicate-ZIP-member warning. Focused
world/twin-physics compatibility and host-catalog checks passed **177 tests in 23.57
seconds**. The separate **MCP 1.22.0** minimum-SDK run passed **216 tests in 26.52
seconds**, retaining the Pydantic unresolved `lifespan` annotation warning. These are
pytest timings, distinct from the wrapper elapsed times in the retained records.
All jobs completed normally with exit zero and a clean checkout at the recorded identity.

Documentation/privacy, assets, catalog, verifier shell syntax and whitespace checks,
wheel/sdist builds and `npm pack --dry-run` passed. Inspection verified all six actual
command-output log hashes and endings against the summary, whose SHA-256 is
`958c4d3d5deecae5325857da05f5b9c57e95408022d84120005f66ccb9f0e30e`.
The separately verified identity record lists the commands' tool versions (Python 3.12.3,
uv 0.12.14, Node v26.13.0 and npm 11.20.0) and package sizes/digests: a 118,792-byte
wheel and 288,025-byte sdist. These are build results, not package publication.

This candidate integrates source `7454f4466788d8acef7e3d8e900dd021c7d7c167`.
The candidate12 comparison records an identical physics helper, with extension differences
limited to a setup docstring and rate diagnostic; it does not claim a byte-identical
extension or a new native run. Exact integrated reopening of a newly gated PhysX save
and affected build/mode checks remained pending at this CPU handoff. Legacy ungated
PhysX graphs retain their documented default/cached warm-up behavior. The original
`166e10d` wrench-header failure and `20f2953` stepping stall remain historical evidence;
CPU passes alone do not close native defects or qualify clean-machine/full-installer use.

### CPU and package follow-up after legacy MCP error reporting (2026-11-02)

At exact commit `826b84b69f04a080882c3ef5894877f389af2938` (tree
`2fc04a2805af4fdecb94bdeac7ecf55bd1861ef0`), the full host suite passed **1,358 tests
in 169.47 seconds**, retaining the intentional duplicate-ZIP-member warning. Focused
gateway error-envelope, catalog, isolation, refresh and dispatch-hook checks passed
**113 tests in 25.74 seconds**. The **MCP 1.22.0** minimum-SDK run passed **234 tests
in 28.64 seconds**, retaining the Pydantic unresolved `lifespan` annotation warning.
This expands the prior 216-test minimum-SDK selection with **18 error-envelope cases**;
it is not the same test selection with a relabelled count. Durations are pytest timings;
wrapper elapsed times remain separate. All six jobs exited zero at a clean checkout.

Documentation/privacy, asset, catalog, shell-syntax and whitespace checks, wheel/sdist
builds and `npm pack --dry-run` passed. Inspection verified the six actual-command-output
log hashes and endings, plus the identity, package/tool and function-comparison records,
against summary SHA-256
`8b651d3a85e18686e155c910e7f708c4693296ae44b74e855b315221cb404af2`.
The package/tool record lists Python 3.12.3, uv 0.12.14, Node v26.13.0 and npm 11.20.0,
and the sizes/digests of the 119,119-byte wheel and 289,649-byte sdist. Nothing was published.

Source `7cdd54fbc2a2879695b865ec38746e2d11c1b441` corrects legacy transport/bridge
failure reporting to MCP `isError=true`, retaining the diagnostic payload and uncertainty
about partial execution. The changed gateway functions are AST-identical to that source;
the complete combined server retains separate project-asset routing and is not byte-identical.
These CPU protocol checks do not qualify actual native success/error envelopes, which
remained pending at this handoff. They do not fix the exact `2c8ce63` Newton 6.0.0.1
headless/windowed native failures or the original `20f2953` stepping stall. Earlier native
and CPU records retain their own identities; no clean-machine/full-installer claim follows.

### CPU and package follow-up after physics-state diagnostics (2026-11-02)

At exact commit `ba1d4b256a011d00787fe32b2eb85cecec8f8812` (tree
`be0be9e2523a4c307336a6f9cb09a80285c32e2f`), the full host suite passed **1,380 tests
in 171.11 seconds**, retaining the intentional duplicate-ZIP-member warning. Focused
step-failure, scene USD, scene gateway and host-catalog checks passed **123 tests in
24.90 seconds**. The **MCP 1.22.0** minimum-SDK selection passed **234 tests in
28.45 seconds**, retaining the Pydantic unresolved `lifespan` annotation warning.
These are pytest durations; wrapper elapsed times are recorded separately. All six
jobs completed normally with exit zero, and the exact checkout remained clean.

Documentation/privacy, asset, catalog, shell-syntax and whitespace checks, wheel/sdist
builds and `npm pack --dry-run` passed. Inspection verified all six actual-command-output
log hashes and endings, plus the identity and package/tool record hashes, against summary
SHA-256 `a567f3304ca5e324b80b35ef2f2aa514a437d86f3bb310d918a881a264bfff39`.
The package/tool record lists Python 3.12.3, uv 0.12.14, Node v26.13.0 and npm 11.20.0,
plus sizes/digests for the 119,389-byte wheel and 291,632-byte sdist. The packages were
built locally, not published.

The integrated `physics.py` and `advanced.py` bytes match submitted source
`f55c64f8e51b192a11a32b1cce9a75b8801db8d2`; the catalog and new step-failure test also
match. The combined contract retains the separate project-asset routing, so the complete
contract file is not byte-identical to that source. The change reports invalid observed
runtime data as `PHYSICS_STATE_INVALID` and includes partial-step clock/phase diagnostics
after native execution starts. It neither rolls back nor automatically retries; elapsed
simulation time is not proof of valid physics. Existing `DEVICE_ERROR` quarantine remains.
Fresh integrated native qualification was **pending at this CPU handoff**. These CPU
results do not remediate the Newton 6.0.0.1 native failures, the later 6.0.1.0 nonfinite-state
failure, or the original `20f2953` stepping stall. Historical native/CPU identities and
clean-machine/full-installer limits remain unchanged.

### CPU and package follow-up after physical camera defaults (2026-11-02)

At exact commit `1f21407eacd02aa1387aa4348611aa60b7848a06` (tree
`c8b73b8a4a441978dee7a94d9d9bfb3472d7c717`), the full host suite passed **1,390 tests
in 170.25 seconds**, retaining the intentional duplicate-ZIP-member warning. The selected
focused run passed **97 tests in 24.95 seconds** across camera units, sensor lifetime,
advanced scene tools, scene gateway and host catalog. Its recorded command selects five
test files; this differs from the source worker's 117-test selection and does not relabel
that result. The **MCP 1.22.0** minimum-SDK run passed **234 tests in 28.38 seconds**,
retaining the Pydantic unresolved `lifespan` annotation warning. Durations are pytest
results; wrapper elapsed times remain separate. All six jobs exited zero normally at
a clean exact checkout.

Documentation/privacy, asset, catalog, shell-syntax and whitespace checks, wheel/sdist
builds and `npm pack --dry-run` passed. Inspection verified the six actual-command-output
log hashes and endings and both supporting record hashes against summary SHA-256
`7c8c59e4b92c329d14cac856d73b2ed3b2d8b482475627bc7a3a269b369aa2df`.
The package/tool record lists Python 3.12.3, uv 0.12.14, Node v26.13.0 and npm 11.20.0,
with sizes/digests for the 119,458-byte wheel and 292,238-byte sdist. Built locally does
not mean published.

The integrated `sensors.py`, `advanced.py`, simulator catalog and camera-unit test bytes
match source `1c48532e21e8707fa48aba62a524fb62d77a1b43`. The combined contract retains
project-asset routing and is not byte-identical to the source contract. The change gives
newly created cameras 0.01–1,000 metre clipping defaults converted to stage units, refuses
invalid units before mutation, and preserves existing/reopened camera settings. Native
camera qualification was **pending at this CPU handoff**. No box-collider adapter shipped.
These results do not resolve prior ROS/Fabric black output, Newton solver/native failures
or the original `20f2953` stepping stall, and do not establish clean-machine/full-installer
qualification. Earlier CPU and native evidence retains its own source identity.

## Live UR5e baseline

These checks modify the stage and move a simulated joint. Use a disposable simulator session and a free port, and
confirm which bridge is selected before running them. This example reuses an already installed Sim 6.2 interpreter;
set the path for your machine or omit `UR5E_DT_PYTHON` to use this repo's `.venv`.

```bash
export UR5E_DT_PYTHON=/path/to/isaacsim-env/bin/python
export UR5E_DT_PORT=18766
./run.sh --headless --port "$UR5E_DT_PORT"
# In another terminal after the app is ready, with the same environment:
./verify.sh
"$UR5E_DT_PYTHON" scripts/smoke_tools.py
```

`verify.sh` checks the workstation baseline: UR5e, RG2, QuickChanger and Vention scene prims; observed
robot-chain rigid bodies with gravity disabled; the six expected joint limits; and USD drive stiffness 100,
damping 10 and max force 200 (the project's default drive values). It commands the elbow by -0.1 rad and requires
the achieved displacement and return error to be within 0.02 rad, with finite observed joint positions. Joint limits
and drive attributes use a 0.01 tolerance. These are regression checks for this shipped configuration, not a
general test for arbitrary robots or proof of hardware fidelity; intentionally different drive settings require a
different expected baseline.

The motion check schedules a bounded Kit coroutine through the existing persistent
`run_python` session, then polls for its measured result. It awaits ordinary Kit frames;
it does not call nested `app.update()`. It requires the reopened stage to be stopped,
waits for a previous native Stop to settle, and refuses stage/timeline changes,
non-finite joints or a stalled clock. Use exclusive control of this disposable scene
until the check finishes: the bridge's per-call admission lock does not cover the
whole scheduled coroutine. A lost host connection does not automatically cancel a
Kit task; the check also has its own 90-second and 12,000-frame bounds.

The check exports a missing reference checkpoint from the current stage, checks that the exported file uses relative
asset references, and requests an asynchronous reopen. A later scene check verifies the expected prims are present;
the `opening` result itself only means the open was scheduled. A regenerated checkpoint's SHA-256 can differ between
machines and is **informational**, not a required match. The checks do not prove every physical behavior, camera,
ROS topic, or visual result. `scripts/smoke_tools.py` exercises additional twin tools and also rebuilds the stage;
review its output for warnings and its documented exclusions.

To check a different installed release, run `scripts/smoke_tools.py --sim-version 6.1` with a host Python that can
import the MCP package and `UR5E_DT_PORT` pointing at the selected disposable session. The option changes only the
expected app-version prefix; it does not establish compatibility or change the simulator installation. Check the
package version separately: the app's reported version may omit a fourth version segment. See
[compatibility](COMPATIBILITY.md) for the results obtained so far.

The validator is [`scripts/validate_ur5e_check.py`](../scripts/validate_ur5e_check.py). It rejects missing,
malformed, non-finite or out-of-range observations, and `verify.sh` exits nonzero if a check or its response fails.
If a check fails, inspect the named result and the simulator log before changing limits.

## Last live check

### Windowed viewport reset comparison

On 2026-10-31, the same windowed reset sequence was run three times each against base `d238989d` and PR head
`0e3521c7` on Ubuntu 24.04.5, RTX 5080, NVIDIA driver 580.178.04 and Python 3.12.3. Both used Isaac Sim 6.2.0.0,
Kit `110.4.0+feature.386251.187cf21b.gl`, `omni.kit.widget.viewport-110.0.1`,
`omni.kit.viewport.utility-2.1.2` and `ur5e-dt-1.0.0`, with ROS 2 Jazzy in isolated domain 197.

Each cycle started the UR5e/workstation scene, added FMB1 objects and their pose publisher, played and stopped the
timeline, started and stopped a 10-fps viewport recording, loaded Simple Room, deleted the objects, called
`ur5e_dt.new_stage`, and rebuilt the UR5e/workstation scene. Immediately before reset, the active camera was the
valid `/World/person_view`, the `/workspace_camera_sim` publisher was active, recording was stopped and the timeline
was stopped. The [original complete traceback](evidence/viewport-reset-2026-10-31.log) is preserved with its local
installation prefix replaced by `<isaacsim>`.

Both revisions logged the same `NoneType.GetCamera` exception once on each of the three resets, inside the viewport
extension while the old stage was being destroyed. After every reset, the active camera was a valid
`/OmniverseKit_Persp` and viewport frame-change events continued. After each rebuild, the active `/World/person_view`
was valid, rendered viewport captures succeeded, a separate ROS subscriber received a 1280-pixel camera image, and a
test cube dropped under gravity. After the third rebuild on each revision, selecting the workspace and person cameras
produced distinct rendered images. This supports recovery for this sequence and machine; it is not proof that all
stages, GPUs, recording states or Isaac versions recover. The exception is not suppressed, and its underlying cause
remains [tracked in the roadmap](ROADMAP.md#1-reliable-baseline-and-documentation).

The comparison also found that the existing real-time recorder sent every viewport redraw to ffmpeg: a roughly
one-second recording at 10 fps produced an 11.8-second file on the base and an 8.8-second file on the original PR
head. With the recording pacing change in this PR, three windowed 10-fps recordings each encoded to 1.0 second for
1.06–1.17 seconds of measured time including bridge calls and finalization. A windowed 30-fps recording encoded to
2.03 seconds for 2.09 seconds measured through the stop request. After this change, three more reset/rebuild cycles
also recovered rendered frames, valid cameras, ROS camera images and falling-cube physics. These are local simulated
checks, not a guarantee for all rendering rates or machines.

For the frame-scheduling regression, a controlled 10-second, 31-Hz input to a 30-fps encoder produced 299 distinct
frames out of 300 after the fix; the previous scheduling produced 155. This check decoded the MP4 and does not replace
a live viewport test at that exact rendering rate.

On 2026-10-31, a windowed Isaac Sim 6.2.0.0 session on Ubuntu 24.04.5 / RTX 5080 with driver 580.178.04 used the
MCP twin driver tool to start the system ROS 2 Jazzy UR driver in mock-hardware mode in isolated domain 197. Both
the joint-state broadcaster and scaled trajectory controller were active; a ROS `FollowJointTrajectory` request
succeeded and changed the mock elbow joint state from 0.0 to 0.05 rad. The MCP tool stopped the driver and status
returned `driver NOT running` and `controller_manager not reachable`. No physical robot was used. This verifies a
mock ROS controller and joint states, not end-to-end tracking of a physical robot.

On 2026-10-30, the verifier passed on Ubuntu 24.04 / RTX 5080, NVIDIA driver 580.178.04, and Isaac Sim 6.2.0.0 in a
disposable headless session. Changing `wrist_1_joint` damping from 10 to 4 caused the validator to fail with the joint
and field named; the value was restored. The simulator also logged the previously observed nested `app.update()`
warning; the broader rare-hang investigation remains open.

In a 6.2 session with the shared-bridge implementation in `382d2cb`, port 18766 served all 23 UR5e tools and two
temporary autoloaded packs, one of them policy-bearing. Both packs reloaded and remained callable when the twin was
disabled and re-enabled; all 23 twin tools returned. In a separate session, the policy-bearing pack was reloaded,
`./verify.sh` passed, and `scripts/smoke_tools.py` passed 20/20 checks. The latter still reported the known narrow-strip
warning for `randomize_single_object`; its headless run also produced an MP4 using the twin's real-time viewport
recording path. These checks exercised simulated scene behavior, not real hardware or every ROS,
windowed, GPU, OS, or Sim-version combination. No training was run. The generic `sim.*` pack was still a design at that source revision and was
not part of these tests; current implementation evidence is linked below.

A separate direct Kit launch, without the `run.sh` deferral handshake, used `ISAAC_MCP_PORT=0` and
`UR5E_DT_PORT=18766`. Its bridge status reported port 18766 and discovery returned all 23 twin tools. The ROS library
environment was supplied for this check, but ROS topics and the broader twin smoke test were not exercised in that
direct-launch session.

With `./run.sh --headless --port 0` (when the launcher enabled the twin by default), a separate headless session bound an OS-assigned port and advertised it in the
runtime portfile. All 23 twin tools were present before and after disabling and re-enabling the twin on that endpoint.

## Generic scene API validation

For the current MCP implementation and its separate live results, see [project scene evidence](evidence/project-scene/README.md). The historical standalone check below exercises selected underlying Kit/USD
APIs in a **new headless process** with a synthetic scene containing Xforms, cubes, a sphere, a scope and an inactive
prim. It creates a new stage in that process and closes the app afterwards; it does not connect to or alter an existing
simulator session or train a policy. It does not explicitly enable the UR5e extension. Starting Isaac Sim uses the
machine's normal graphics and GPU resources.

From the repository root, use an interpreter with Isaac Sim installed and an accepted EULA:

```bash
(
  ISAACSIM_PYTHON="/path/to/isaacsim-env/bin/python"
  validation_log=$(mktemp "${TMPDIR:-/tmp}/isaac-mcp-generic-scene.XXXXXX") || {
    printf 'Could not create validation log.\n' >&2
    exit 1
  }
  if OMNI_KIT_ACCEPT_EULA=YES "$ISAACSIM_PYTHON" scripts/validate_generic_scene.py > "$validation_log" 2>&1; then
    validation_status=0
  else
    validation_status=$?
  fi
  if [ "$validation_status" -eq 0 ]; then
    if ! grep '^GENERIC_SCENE_VALIDATION=' "$validation_log"; then
      printf 'Validation success marker missing.\n' >&2
      validation_status=1
    fi
  else
    printf 'Validation process failed (exit %s).\n' "$validation_status" >&2
  fi
  printf 'Full log: %s\n' "$validation_log"
  exit "$validation_status"
)
```

The command preserves a nonzero simulator exit status and also fails if the marked JSON line is missing; it prints the
log location in either case. The explicit `exit` stays inside the subshell. The script reports assertion failures with a
nonzero exit status, while Kit controls startup failures. The JSON lists the checks, observed helper behavior, Isaac Sim
distribution version, Python version and platform. The check validates selected building blocks
for `stage.info`, `stage.tree`, `prim.get` and `prim.find`; it does not test the registered MCP tools, an MCP bridge, deep-tree
limits, physics, capture, real hardware, or every stage and installation type. Isaac's deprecated `core.utils` helpers
are optional observations; the USD checks run directly against the active Kit context.

On 2026-10-30, the check passed on a working branch based on `584aa31`, using Ubuntu 24.04.5, kernel 7.0.0-38,
Python 3.12.3, Isaac Sim 6.2.0.0, an RTX 5080 and NVIDIA driver 580.178.04. The anonymous stage had a nonempty `anon:`
URL. The helper rejected the USD `Cube` type filter; it returned no matches for an anchored expression and for an
invalid expression, while a simple alternation matched. A separate run with an intentionally wrong default-prim
expectation printed the assertion and exited with status 1. See [the design observations](GENERIC-SIM-TOOLS.md#324-first-live-scene-inspection-observations)
for the implications and remaining gaps. This is one version and machine, not a portability claim.

## Other checks

- For a windowed workflow, confirm the intended window and capture with the [host tools](TOOLS-HOST.md).
- For ROS transforms, use `scripts/verify_tf.sh` with the matching `config/ros_dds.env` profile.
- Before a training run, obtain operator authorization and check GPU capacity. Use the
  [Isaac Lab guide](TOOLS-ISAACLAB.md) for status, metrics, and guard behavior.
- See [ROADMAP.md](ROADMAP.md) for remaining work.
