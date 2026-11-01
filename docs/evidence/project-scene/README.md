# Project assets and generic scene evidence

**Owner-approved first slice, 2026-11-01.** PR #11 implements six asset tools and 31 generic
scene tools. The owner chose to merge PR #10, then review/merge this slice with **isolated
untrusted loading, authenticated remote upload, presets/manifests and remaining fork parity
still unmet**. Those are follow-ups, not completed capabilities. No PR is merged by the agent.

The trusted workflow is exercised through real MCP with original multifile USD/URDF/USDZ
fixtures and a separately licensed real Agilebot robot. Root checks, snapshots, dependency
inspection, filters and managed launch are **not an OS sandbox**. Untrusted configuration
fails closed; no remote upload service or unrestricted fallback is advertised.

## Evidence map and scope

- [Per-tool behavior](TOOL-COVERAGE.md), [release/backend results](RUNS.md),
  [compact call inventories](RUN-SUMMARIES.json), [installed package records](INSTALLATIONS.json).
- [43-tool fork comparison](../../FORK-COMPARISON.md), [setup and schemas](../../TOOLS-SCENE.md),
  [compatibility](../../COMPATIBILITY.md), [plan and deferred gates](../../plans/ASSET-SCENE-END-TO-END.md).
- [SHA256SUMS](SHA256SUMS) verifies the small committed evidence files.
- Full redacted transcripts, native/CPU logs, failure history, session identities and copied
  reproduction scripts are in the **draft `project-scene-evidence` GitHub release**.
  [Release asset URLs and SHA-256](RELEASE-ASSETS.md) identify each bundle. Draft assets require
  repository access; the owner can use `gh release download project-scene-evidence`.

Paths use `<worktree>`, `<repo>`, `<home>`, `<sim-install-…>` and `<host>` placeholders. The
records remain observations of the actual runs, but their redacted bytes differ from originals.
`REDACTED-SHA256SUMS` inside each bundle verifies distributed bytes; source/image/response hashes
inside historical transcripts identify the original observations and must not be treated as
hashes of redacted JSON. Saved robot geometry, private native dumps and unreviewed images are
omitted, including inline base64 screenshot payloads in transcripts and copied diffs. Large recordings and snapshots stay outside Git. Selected images below are unchanged
application-window captures, each under 400 KB; no terminal or personal desktop content is kept.

The privacy guard runs with `scripts/check_docs.py`, including nested gzip/tar/zip payloads,
member names and metadata. Six unchanged vendored UR5e assets contain older **third-party**
provenance paths; their exact original hashes are the sole exception because the owner forbids
editing those assets. They contain none of the owner's requested identifiers. Any changed
asset or new file loses that exception. Screenshot pixels require separate visual inspection.

## Revisions and actual environment

Ubuntu 24.04.5, kernel 7.0.0-38-generic, RTX 5080 (16,303 MiB), NVIDIA 580.178.04, Python 3.12.3;
host MCP 1.30.0, minimum SDK 1.22.0, CPU OpenUSD 26.11. Actual Kit/extension/loaded library
identities accompany stage-info responses. Pip metadata alone can differ from loaded Warp.
Existing installations were reused read-only; these were separate disposable processes and
settings, not freshly rebuilt pristine installations. Kit writes ordinary logs/caches.

| Product code revision before privacy squash | What it establishes |
|---|---|
| `f32876520ebaee97ef56c5c4422f84fb833bd8f8` | Original copied-CPU attachment adapter; historical37-tool6.2 and agent proof. |
| `1135ec42c88c3dfedfad7966bc606f0e4d8ba39f` | Four-component build qualification, older LiDAR, scoped Newton adapters, trusted USDZ, async World initialization. |
| `9fe39d4fee7605733da54c127092cae3f20c75a6` | Reuse only verified identical snapshots; full PhysX tool/stress suites and independent tasks on older builds. |
| `faaf73d5eb7b135cd703fb56da1369f85d346e37` | Current-target refusal details, default-on scene opt-out, cold CPU readback priming and safe stage teardown; full 6.1 suite and independent agent. |
| `08cf0c9` | Reopenable exports omit native runtime render graphs; CPU preservation/refusal tests and live6.2 suite plus6.0.0 fresh-process reopening. |

Old commit identifiers are retained as historical run identities. The owner requested removal
of private paths from **reachable PR history**; the implementation branch is therefore squashed
onto the updated planning branch. Source-hash manifests and frozen source patches in the bundles
map earlier runs to their exact code. Final PR-head validation and product-tree identity are
reported in the PR body and final validation release record. History rewriting does not assert
that previously fetched copies or GitHub caches have been erased.

## Automated review validation

CPU suite: **902 passed** (one intentional duplicate-ZIP-member warning). Minimum MCP1.22.0:
**203 passed**, retaining the upstream Pydantic `lifespan` warning. Wheel/sdist, npm dry-run,
documentation/catalog and privacy checks pass. Real SDK tests cover stale-target details,
scene opt-out before/after refresh, per-server state, hidden-call refusal and notifications.
Logs and exact commands are in the compatibility bundle.

## Measured behavior and retained failures

All37 tools were called through real MCP on each of the five installed PhysX releases. Exact
cases and revision differences are in RUNS.md. Each release passed a 217-capture stress sequence
across three resolutions, timeline transitions and exact physics steps, with known LiDAR plane
geometry and coordinate transforms. Independent actual-use tasks on all five releases imported
both approved projects, measured fall/rest and joints, captured real images, saved output and
checked source/sibling preservation. These tests establish the stated workflows, not every
parameter, backend, lifetime or sensor synchronization case.

Failures are retained: session-layer physics/writeback divergence; old borrowed-GPU capture
CUDA700/native crashes; initial snapshot quota exhaustion;6.0.0 cold first-image latency;
6.0.1 native sensor-stage reset crash; too-short sensor warm-up; invalid serialized native
render handles; strict joint target tolerances and test-harness version/schema mistakes.
Retests are identified separately. A failed strict joint tolerance is not relabeled as success
because a command was accepted. An expected guard refusal is not working-capability evidence.

Fresh-process6.0.0 reopening now loads the saved geometry and measured resting body correctly,
and fresh sensor creation succeeds after native graph records are omitted. **Camera readout
still returns `NOT_READY` on that reopened-stage workflow**, including extended playing warm-up
and a separate render-tree-free diagnostic copy. New-stage camera stress and cold-first-frame
retests pass. This remaining reopen/sensor edge is unverified functionality, not a successful
end-to-end capture; its failures and alternatives are retained for follow-up.

The save fix exports a private composed copy, omitting only recognized native SyntheticData
pipelines and pack render products. User Action Graphs and sensor definitions remain, the live
stage stays unchanged, and unknown native-pipeline content refuses publication. New sensors
need ordinary rendered warm-up after reopening. Exports retain texture dependencies and are
**not physics/controller checkpoints**.

UR5e's World constructor uses the extension async initialization path and restores the terminal
flag before awaiting. The review runs retain warnings instead of suppressing them.6.0.1's
fresh cleanup retest completed sensor stress and20/20 UR5e smoke after the earlier native crash.
The final 6.2 `./verify.sh`, smoke, motion and existing `/physicsScene` checks are recorded in
final validation. Only test-owned processes are stopped. The user checkout, other sessions and
`exts/ur5e-dt/assets/**` remain unchanged.

Newton's qualified MuJoCo paths were separately tested on all five installations for two cycles
of actual fall/rest, joint movement, measured step timing and pose exports. Full37-tool and
independent-agent coverage on **every Newton backend/version is still missing**. Isaac Lab live
training, physical hardware, other drivers/GPUs, future releases and exhaustive sensor lifetimes
are not newly verified here. CPU tests retain Lab/bridge/gateway/managed-session regressions.

## Actual application screenshots

Original6.2 independent task before and after automatic physical settling (PID1672242,
window46137357, historical adapter `f328765`; runtime checks are in its transcript):

![Before physics](images/agent62-before.png)
![After physical settling](images/agent62-rest.png)

Later independent tasks have selected-window captures on
[6.0.0.0](images/agent6000-rest.png), [6.0.0.1](images/agent6001-rest.png),
[6.0.1.0](images/agent601-rest.png), and [6.1.0.0](images/agent61-rest.png).
Their reports record window IDs/PIDs, tool results and original hashes. Images were inspected
for actual scene content and visible private paths. Screenshots prove visible output; separate
runtime state, velocity, joint and timing observations support physics conclusions.

## Research and provenance

The fork at `bfcd2111a9a98657ea87ba3be8564ea19c1a89e6` has **43 actual registrations**,
including 12 simulation tools. README, tool definitions/handlers, version adapters, tests,
examples, limitations and MIT license were inspected. Its CPU suite passed 349 tests with its
declared MCP<2 dependency; a separate MCP2 mismatch is retained. A disposable fork6.2/PhysX run
made 24 real MCP calls across 18 tools (fall/rest, robots, camera and LiDAR), not all 43. No paid
service was used. The other three requested pins were inspected, not run; remote default heads
still matched all four pins at research time. See comparison and bundle research records.

No upstream implementation code or robot geometry is copied. Original fixture generation is
in `scripts/make_scene_fixture.py` (CC0); package fixtures are also original CC0. The real robot
is [Agilebot GBT-C5A at33281110](https://github.com/sh-agilebot/agilebot_isaac_usd_assets/tree/33281110dd75017fcb821ef4a7316960c0702e18/gbt-c5a),
five USD layers and three textures, separately audited under BSD-3-Clause. The vendor license
and third-party statement were checked; [notice](../../provenance/agilebot-LICENSE) retained.
Workr candidates were not used on the strength of root MIT alone. Installed OmniPBR remains an
external trusted NVIDIA dependency. Source manifests in agent records verify unchanged inputs.
[MIT attribution](../../provenance/whats2000-LICENSE) and the separate
[omni-kit-mcp permission question](../../VENDORING.md) remain recorded.

Official NVIDIA camera migration/known-issue/asset-validation/ScriptNode documents and OpenUSD
dependency documentation were checked against installed APIs. ComputeAllDependencies and
opt-in ScriptNode behavior actually ran; NVIDIA's complete asset-validation suite did not.
Those mechanisms are not containment. No audit or hostile-USD security guarantee is claimed.

## Reproduction

Use a disposable simulator and explicit approved roots as in TOOLS-SCENE.md. Inventory bridges,
windows, GPU/RAM and target PID first. `scripts/validate_scene_tools.py`,
`validate_scene_equivalents.py`, `validate_scene_edges.py`, `validate_scene_sensors.py`,
`validate_scene_clear.py`, `validate_scene_sessions.py` and `validate_scene_reopen.py` are real
MCP test clients. Full copied run orchestrators are in the release, not the Git evidence tree.
Replace neutral path placeholders with your own approved locations; never target a user's stage.

```bash
python3 scripts/check_docs.py
python3 scripts/gen_host_catalog.py --check
# In mcp/, with test/isaaclab/scene-test dependencies:
pytest -q
uv build
# Explicit disposable port and one-time trusted-project config:
ISAAC_MCP_ASSET_CONFIG=/approved/config.json python scripts/validate_scene_tools.py --port 19063 --output /private/core.json
```

The release includes complete commands, selected ports/domains/PIDs, setup, schemas, calls,
responses, timestamps, pass/fail/refusal distinctions, cleanup records and source identities.
Isolated loading, authenticated remote transport and preset contracts remain this plan's work;
non-security parity follow-ups are assigned to PR #12. Configured Claude is authorized for
independent remote tests when that deferred workflow is resumed; no remote test success is claimed.
