# Plan: project USD assets and generic scene tools, end to end

**Status: proposal, not implemented.** Research checked 2026-11-01 UTC against `main` at
`d8954ad374ae2fed4d8c2a45af0d7e96f655566f`. This document plans the two requested features:
bringing project USD files into Isaac Sim and operating a generic scene through MCP. The planning PR does
not implement or test those features. Its citations are research, not claims that the other projects were run.

The intended delivery is **one implementation PR**, developed in reviewable commits using the milestones below.
Keep that PR open until the complete applicable acceptance checklist, real-use demonstration and evidence are
available. If a capability cannot be made safe or tested, leave it explicitly disabled and the corresponding
end-to-end claim incomplete; do not label a discovery-only implementation as the finished feature. The user may
choose to split the implementation for review, but a partial merge does not satisfy this plan's finish line.

This is the execution plan for [asset presets and project-folder discovery](ASSET-PRESETS.md) and the
[generic Sim tools design](../GENERIC-SIM-TOOLS.md). Those documents contain useful details and historical
observations; recheck their version-specific assumptions against the merged code. The trusted-project profile
below is a proposed, explicit addition to the older asset plan, whose untrusted-import restrictions remain in
force. Its independently mergeable PR sequence is replaced here by one feature PR with internal milestones.

## The user-visible outcome

A user can put a robot, object or scene in a project folder and ask an agent, for example:

> Find the robot USD in this project, add it to the selected Isaac Sim scene, tell me its location and whether it
> has collisions and joints, and show me the result.

On a supported configuration, the agent should be able to do the following through documented MCP tools:

1. Identify the user's project folder, get its access approved once, and list available USD files without opening
   them. Present paths and meaningful errors rather than requiring the user to write Python or create a manifest.
2. Select a running Sim session and verify its bridge, PID, stage and readiness, or start a separate disposable
   session with the existing managed-session safeguards. Never guess a port or switch an active user scene silently.
3. Select a file, explain its trust profile, and import it under a new prim path with a requested pose. Support a
   normal project asset made of a root USD plus relative layers, meshes/materials and textures, subject to the
   selected profile and explicit resource limits. Report the actual result and any missing dependencies.
4. Inspect the stage hierarchy, the imported prim, world/local transforms and authored physics. Create or move a
   simple object through typed tools; capture the real viewport; save a separate scene copy on request.
5. Tell the user what was observed, what remains unknown, where the output and screenshot are, and which session
   received the change. An authored collision or articulation is not a guarantee of stable simulation.

The normal same-machine path should need a one-time project-folder/trust approval, not hand-authored tool scripts.
The host may suggest its verified workspace root, but a path supplied by the model alone must not grant itself
access. Configuration must also work when the MCP host starts outside the project directory. The agent's file
access does **not** imply that the MCP server or Kit can see the same path. For a separate machine, support a
user-approved shared mount or the authenticated transfer workflow below; explain which applies.

Initially target Linux/X11 and Isaac Sim **6.2.0.0**. Include `.usd`, `.usda`, `.usdc`, `.usdz` and common
multifile projects in the finished workflow. Enable `.usdz` only after archive limits and dependency handling
below are implemented and tested; until then reject it with a clear reason and mark that format incomplete. Do
not claim that every 6.x version, renderer, operating system or asset is supported from this one baseline.

## Existing code and boundaries

- PR #9 merged desktop input, optional managed Sim sessions, the optional UR5e panel and offline discovery of the
  23 UR5e tools. `sim__launch`, `sim__list`, `sim__status` and `sim__stop` manage processes; they are not the
  generic scene tools proposed here. Managed launch isolates output/ports/settings, but it is **not a sandbox**.
- `ur5e_dt__*` remains available with existing behavior; its workstation and object tools are specific workflows.
  Do not modify `exts/ur5e-dt/assets/**`, remove its tools, or require its panel for generic workflows.
- The shared Kit bridge can host separate tool owners. The planned `assets` and `sim` packs do not yet exist.
  The host's packaged catalog currently covers UR5e; extend discovery deliberately instead of claiming new tools
  appear offline automatically. `ISAAC_MCP_LOCAL_TOOLS` filters local tools, while `ISAAC_MCP_NAMESPACE` and
  `ISAAC_MCP_BUILTINS` affect the host's bridge view. Hiding a tool or setting an annotation is not access control.
- The raw bridge has powerful file and Python built-ins and is not an authenticated remote file service. Do not
  use unrestricted `run_python` or `put_file` as the advertised generic feature or assume a filtered host view
  secures a bridge socket. Protect every entry point able to load USD or author asset-valued attributes.

## Trust and file-location model

The usability goal and the safety boundary must be visible to the user. Implement and test these as distinct
profiles; a tool argument from the agent must not promote a file or session to a more trusted profile.

### User-trusted project workflow

For files the user personally trusts, support an explicitly enabled project root and import into the user's
chosen existing or managed Sim session. One-time configuration or a client-supported approval flow grants a narrow
root; the agent can select an approved root ID and relative path, not create or widen roots itself. Make the common
case easy: suggest the verified project root when the agent and service share that workspace, and give an actionable
setup path when they do not. A consent or configuration interface must actually be implemented for each advertised
client; do not pretend an MCP tool can display a universal approval dialog.

Root checks, size bounds and dependency inspection reduce mistakes, but an ordinary existing Kit process retains its
normal filesystem, network, resolver, plugin and extension permissions. **This profile is not a sandbox for hostile
USD**, including files inside an approved folder. Clearly show that choosing it trusts the asset and dependencies;
reject or surface unknown/missing dependencies and never call a scan proof of containment. Confirm exactly what
ScriptNode and Python-scripting settings do in the supported Kit release before promising that scripts are disabled.
NVIDIA documents that an OmniGraph ScriptNode can store Python in USD and execute it when evaluated:
[ScriptNode overview](https://docs.omniverse.nvidia.com/kit/docs/omni.graph.scriptnode/latest/Overview.html).

### Isolated untrusted workflow

For downloaded or otherwise untrusted files, retain the fail-closed model in [ASSET-PRESETS.md](ASSET-PRESETS.md):
bounded, immutable snapshots; inspection **and the entire load/use lifetime** in a verified low-privilege isolated
worker/Kit; filesystem and network restrictions; controlled resolvers/plugins/scripts; resource limits; and no
access to secrets or unrestricted bridge tools. Verify enforcement rather than relying on environment variables,
preflight or UI prompts. If an isolation mechanism or complete dependency handling cannot be proved on a platform,
do not enable untrusted inspection/import there. The loaded stage remains in that isolated session; it must not be
silently inserted into an unrestricted existing Kit session. Residual native parser/GPU risks remain.

Use a strict profile first, then a separately enabled contained profile for common relative multi-file assets only
after transitive reference, payload, sublayer, material, texture, variant and plugin behavior is demonstrated.
OpenUSD's [dependency APIs](https://openusd.org/release/api/dependencies_8h.html) are useful inputs to inspection,
but their output alone is not an OS sandbox or proof of coverage for every resolver and extension. Reject unresolved,
outside-root, network or unknown dependencies in the untrusted profile; never silently fall back to trusted mode.

### Remote project files

When the agent's project is not mounted on the Sim machine, provide a client-side helper the agent can run to send
an explicitly approved file set to a dedicated staging service; the model should not have to paste file contents into
chat. Define which process reads the project files and how it obtains consent. Preserve relative paths, use bounded
chunks and total size/file-count/depth limits, hashes and final verification, per-owner quota and expiry, atomic
publish, no overwrite, and reject absolute paths, traversal, symlinks and special files. Bind the upload to an
authenticated user and target session; do not trust an identity or root supplied in a tool argument. Use an
authenticated, authorized transport appropriate to the deployment, not the existing unauthenticated bridge socket.

Local stdio under one OS user and a remote multi-user deployment have different trust assumptions; document both.
Require a real no-shared-mount end-to-end test before calling remote upload supported. If a second host or an
authenticated transport is unavailable, keep that path disabled and record it as unverified rather than substituting
`put_file` or claiming that an agent-local path exists on the simulator host.
For this topology, the agent's local file tools or the companion helper discover files on the agent machine; the
remote broker cannot list that folder until an approved transfer or mount makes it available. Verify that the
chosen client can actually invoke the helper without asking the user to hand-copy files.

Place the authenticated upload endpoint and root-approval policy in a host-side asset broker or equivalent
protected service; do not register upload as an unprotected raw Kit-bridge operation. A trusted local profile may
route scene operations through the existing selected bridge. An untrusted profile needs its own protected endpoint
and isolated Kit with raw/alternate bridge access prevented, including other tool packs that could bypass the
policy. Verify the actual network and process boundary; do not equate `sim__launch` or host-side filtering with it.

## Proposed tool contracts

Names are proposals to finalize against the existing bridge naming rules: dots in canonical names become `__` in
MCP. Keep the existing `sim__launch` group distinct from generic names such as `sim__stage__info`.

| Area | Proposed capabilities and minimum behavior |
|---|---|
| Asset discovery | `assets__roots`, `assets__list`, `assets__scan` and `assets__info`: enumerate only approved roots and versioned preset manifests; bounded, deterministic pages; opaque root IDs and relative paths; explicit truncation/limits. Filename-only discovery must not invoke a USD parser. Distinguish manifest claims from observed metadata and record asset source/license. |
| Transfer | `assets__upload_begin`, `assets__upload_chunk`, `assets__upload_finish` or an equivalent client helper: authenticated staging with the limits above, abort/cleanup and clear unsupported responses. Do not expose arbitrary destination paths. |
| Asset load | `assets__import_usd` and preset-based `assets__load`: explicit selected session, approved root/upload ID, source, prim path, pose and profile fixed by user configuration. Default `on_conflict=error`; return actual path, source/snapshot ID, checks, warnings and observed partial effects. Do not report success for an unresolved reference or missing prim. |
| Scene inspection | `sim__stage__info`, `sim__stage__tree`, `sim__prim__get` and a bounded search: stage/session identity, source, up-axis, units, timeline state, paths, types, active state, local/world transform and bounds where available. Include caps, pagination or truncation and stable error codes. |
| Scene authoring | Create a small supported set of primitive types and place or transform a prim with explicit local/world frame and conflict handling. Use a wrapper or otherwise preserve authored transforms and source files. Refuse unsupported complex/time-sampled transform cases instead of silently flattening them. Destructive delete/reset and save-as require explicit targets and protections; never overwrite by default. |
| Physics inspection | Report authored rigid bodies, collision, mass, joints, articulation roots and simulation readiness as observed/unknown, with bounded output. Read runtime state only when initialized and clearly distinguish it from authored USD data. Check existing physics scenes and avoid silently adding duplicate floors or changing global physics. |
| Timeline and capture | Provide generic play/pause and a tested stepping path if needed for verification; do not re-enter Kit's update loop from a handler. Reuse existing window/capture tools where sufficient; add a generic capture tool only if required and verify the selected viewport/camera. |

Specify coordinate frame, unit conversion, quaternion order and time sample in every pose contract. Test
non-identity rotations, parent transforms and Y-up/meters-per-unit mismatches; reject mismatches by default until a
conversion is verified and never silently apply a second conversion. Preserve the original asset. Bind mutations
to the selected bridge and stage identity; reject a stale target or changed stage rather than editing a different
session. For a running physics body, use a verified runtime-aware operation or refuse with a clear instruction;
do not merely edit an authored USD transform and claim that the simulated body moved. State what can and cannot be
rolled back, and report partial effects if an operation fails after a change.

Expose new tool schemas to agents even when Kit is down, following the existing catalog/proxy pattern where
appropriate. An unavailable or unapproved tool must explain how to enable/select it and must not start Kit or widen
permissions silently. Test namespace filters, collisions, live metadata replacing proxies, `refresh_tools`,
`use_bridge`, `notifications/tools/list_changed`, and per-server/per-session isolation. Keep schemas truthful about
required arguments, side effects and annotations; a `status:error` payload must not be presented as a successful
operation. Make any MCP error-envelope change backward compatible and test actual client behavior.

## Research: useful existing work

The following were read on 2026-11-01; repository claims have not been independently run or security-audited for
this plan. Pins make the review reproducible. Examine approaches first, and record license/attribution before copying
any code or asset. The separate upstream permission issue in [VENDORING.md](../VENDORING.md) remains unresolved.

| Project / source | What to learn or reuse as a reference | Boundary |
|---|---|---|
| [whats2000/isaacsim-mcp-server](https://github.com/whats2000/isaacsim-mcp-server/tree/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6) — MIT; main commit 2026-10-28 | Scene/prim inspection, version adapters, step-and-observe and reporting the actual imported prim; inspect its [scene handlers](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac.sim.mcp_extension/isaac_sim_mcp_extension/handlers/scene.py) and [asset handler](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac.sim.mcp_extension/isaac_sim_mcp_extension/handlers/assets.py). | Its README targets Sim 5.1–6.0.1; it accepts local paths and URLs. Those claims and its loader do not establish 6.2 compatibility or an untrusted-USD security boundary for this project. |
| [omni-mcp/isaac-sim-mcp](https://github.com/omni-mcp/isaac-sim-mcp/tree/949c4886be6e9091ceac8be4272207471b2d3d80) — MIT; main commit 2026-10-13 | Examples of scene setup and agent-facing workflow descriptions for an Isaac Sim MCP extension. | Treat its broad script/command access and older setup examples as integration ideas, not a sandbox or proof of current-version compatibility. |
| [dcc-mcp/dcc-mcp-openusd](https://github.com/dcc-mcp/dcc-mcp-openusd/tree/84595bde4f2dc87ab953688ff5a3123bcec9e88c) — MIT; main commit 2026-10-17 | Typed OpenUSD stage/composition operations and its explicit distinction between native `pxr` and text-fallback results. | A headless OpenUSD adapter is not a live Isaac Sim/PhysX test; a text parser is not a security boundary or proof of full USD composition. |
| [work-r-labs/robots](https://github.com/work-r-labs/robots/tree/ab6aa489855256de07ba46c7b45887fdbc42b607) — root MIT; main commit 2026-06-05 | Candidate real non-UR5e multifile project: its README identifies an ABB CRB15000 USD and a normal load workflow. Use it as an external test candidate, pinned by commit and file hashes. | Inspect the chosen model's source, dependencies, notices and redistribution rights separately; a repository-level license alone does not establish rights for every third-party asset. No claim of Sim 6.2 compatibility until the actual test passes. |
| NVIDIA [asset validation](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/robot_setup/asset_validation.html) and [Isaac Lab USD utilities](https://isaac-sim.github.io/IsaacLab/main/source/api/lab/isaaclab.sim.utils.html) | Compare existing validation rules and stage/prim utility APIs before implementing duplicate checks; report which rules actually ran. | Asset validation reports authoring/readiness issues; it does not prove hostile content safe. Isaac Lab APIs are not automatically available in every Sim-only installation. |
| NVIDIA [Isaac Sim MCP documentation](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/development_tools/isaac_sim_mcp.html) | Its documentation search may help find current extension APIs and examples. | NVIDIA describes a semantic-search reference server; do not mistake its documentation search for a verified live scene-control implementation. |

## Implementation milestones inside the feature PR

1. **Baseline and contracts.** Pin the implementation base, inventory the installed Sim/Kit/SDK versions and live
   bridge, confirm resource headroom and existing processes, and record the baseline tests. Recheck versioned API
   signatures and the current scene/asset plan. Define the trust UX, approved-root setup, session identity,
   coordinate/units/error contracts, capability flags, and a tiny original test fixture with license/provenance.
   Test ordinary same-host setup and failure when no root is approved before adding any loader.
2. **Discovery and inspection.** Implement root listing and bounded filename-only scanning, then generic read-only
   stage/prim/physics inspection. Register the applicable Kit-side packs on the shared bridge for the trusted local
   profile, while keeping upload and root approval in the protected broker; expose truthful offline schemas and
   preserve UR5e discovery. Test all limits, filters, empty/no-stage behavior, session switches and result schemas.
3. **Trusted project import and scene edits.** Implement the explicitly enabled trusted workflow, multifile project
   handling, generic primitive placement/transform and safe save-as. Validate actual prim creation, units, paths,
   dependencies and reported effects in a disposable Sim. Never modify the source USD or replace or clear an
   existing stage by default; edits require the intended stage to be explicitly selected.
4. **Isolated untrusted import.** Implement the snapshot, resolver/plugin/script and OS-enforcement design in a
   dedicated session, with negative controls that prove the restrictions are active. Add contained multi-file
   support only after the dependency and lifetime tests pass. Default to unavailable where enforcement is unknown.
5. **Remote transfer and package format.** Build authenticated client-side transfer and bounded upload staging;
   exercise an actual agent-accessible project that is not mounted on the Sim host. Add `.usdz` after archive path,
   decompression, nested reference and size/ratio tests; until those pass, report it unsupported and incomplete.
   No raw bridge file tool is an acceptable replacement for the remote feature.
6. **Compatibility, docs and release readiness.** Run the matrix and actual-use acceptance below, update the
   tool references, `AGENTS.md` counts, installer/configuration, catalog generator and compatibility guide. Link
   evidence from the feature PR. Preserve the existing UR5e and desktop workflows and clearly list limitations.

Milestones can be developed and reviewed as commits. A milestone is not marked passed just because a stub, mocked
test or screenshot exists: it must meet its own tool-contract and evidence checks. The feature PR must call out any
scope change and remain unmerged if the promised end-to-end path is not complete.

## Verification: tests, screenshots, then actual use

Use real MCP client/server sessions for end-to-end checks; direct bridge/API probes help diagnose failures but do
not replace the MCP-path test. Record pass, fail, skipped and expected refusals separately. Never label a mocked
window or a generated image as an Isaac Sim screenshot.

### 1. Automated and integration tests

- **CPU-only CI:** manifest/schema and path policy, deterministic paging, limits, Unicode/case, nonexistent paths,
  traversal, symlink/rename races, stale roots, hash/size mismatches, upload quotas/authentication/cleanup and
  `.usdz` archive-bomb/traversal cases if supported. Exercise malformed USD inputs only inside the designed test
  boundary. Use mocks where necessary and label them as mocks.
- **MCP compatibility:** tool schemas and required fields, offline catalog and drift checks, `list_changed`, namespace
  filtering, old/new bridges, explicit status/error and partial-change results, two server instances, and selected
  session/stage identity. Run the relevant tests with the minimum supported MCP SDK as well as the current version.
  Test no bridge, wrong port, bridge loss, retries/timeouts and a restart; a timed-out mutation has unknown outcome
  until the agent inspects the stage, not automatic permission to retry.
- **USD and scene:** self-authored tiny fixtures include a standalone object, a multi-layer asset with a real
  texture, a nontrivial parent transform, an authored articulation and a simple rigid body. Test default prim,
  local/world transforms, non-identity quaternion, non-metre/up-axis mismatch, duplicate prim refusal, unsupported
  transforms, inactive/missing prims, capped traversal, no stage, and authored versus runtime physics. Validate a
  saved copy by reopening it in a new disposable session.
- **Safety:** exercise outside-root and nested/variant/texture references, unresolved links, URLs, custom/unknown
  resolvers, script-bearing USD, file replacement after validation, resource exhaustion and upload path attacks.
  For isolated mode, demonstrate the OS/filesystem/network/plugin restrictions and use harmless canaries in
  disposable paths; no unexpected canary file or network access may occur. A passing dependency scan or an empty
  canary alone is not proof of isolation. Confirm raw bridge/builtin bypasses cannot reach the protected session.
- **Live headless:** with actual Isaac Sim 6.2, call every new tool at least once for success and its important
  refusal/error paths in a disposable non-UR5e stage. Confirm a simple independent rigid object visibly/actually
  moves under gravity after the supported step/play path, with numeric before/after observations; verify a missing
  collider is reported instead of being presented as physics-ready. Run the existing UR5e regression baseline in a
  separate selected session and verify generic tools do not alter it.

### 2. Windowed evidence and screenshots

Launch a real, windowed Isaac Sim session on the visible desktop, select it by window ID and PID, and use the actual
MCP tools. A mock UI or virtual-display-only run does not meet this visual gate. Capture uncropped
application-window PNGs at minimum: (a) the initial empty/disposable stage and selected session,
(b) the imported robot/object with its hierarchy visible, and (c) the changed pose and/or reopened saved scene.
Record screenshot paths, dimensions and SHA-256, and verify images decode and show rendered nonblank frames. A
screenshot demonstrates a visible result, not correct physics, source containment or the absence of script effects;
pair it with tool responses, stage observations and the safety tests. Do not capture unrelated windows, credentials
or personal project content. Store small redistributable evidence in the PR only when rights and privacy permit;
otherwise link a reviewer-accessible private artifact with its hash, access method and retention period.

### 3. Independent actual agent use

After tests and screenshots, start a fresh session and have a real MCP-connected agent follow the user prompt above
without `run_python`, direct private APIs or a human manually operating the UI for the agent. Record the natural
language prompt, tool calls/responses, selected session/port, approvals, observed stage/physics state, artifact
paths and final response. A human inspects the viewport and results and records pass/fail. Use both:

1. A fresh local project folder with the small, self-authored multi-file fixture, including a texture, and a new
   session. Verify discovery, import, inspection, a meaningful object placement, screenshot and saved-scene reopen.
2. One pinned, independently licensed real non-UR5e project asset, such as the Workr candidate above after its
   dependency/license review. Inspect its actual imported hierarchy and known physical properties; state explicitly
   whether motion was tested and never claim simulation fidelity from a static screenshot.

Also run the user-approved remote no-shared-mount path on two distinct environments with authenticated transport;
demonstrate that the source is absent on the Sim host before upload, that hashes and relative dependencies match,
and that another user/session cannot access the staged files. A loopback simulation or mocked uploader is useful
coverage but must be labeled as such and does not establish real remote support.

### 4. Versions and evidence record

The first complete GUI/agent run targets Ubuntu 24.04/X11, RTX 5080 and Sim 6.2.0.0, with actual package, Kit,
driver, Python, MCP SDK, asset hashes and bridge/extension versions recorded. In separate environments, run
representative discovery/import/scene-inspection checks for **6.0.0.0, 6.0.0.1, 6.0.1.0 and 6.1.0.0** as well as
6.2.0.0; test the profile and dependency combination actually claimed for each. Mark failures, known async/viewport
warnings and untested combinations in the [compatibility guide](../COMPATIBILITY.md); do not infer support for all
6.x or for Isaac Lab from a Sim-only check. Do not disturb an existing user Sim/BrickSim session: inventory active
bridges and resource headroom, use separate ports/domains/output, and stop only test-owned processes.

Add an evidence index under `docs/evidence/` and link it from the feature PR. For every run record: exact Git SHA,
date, host/OS/GPU/driver, app and package versions, setup and commands, enabled profile/root/transport, target
session and stage identity, input hashes/licenses, tool-call transcript, expected versus observed results, log and
screenshot locations/hashes, warnings, cleanup and reviewer reproduction steps. Separate automated/mock results,
real simulator results and the independent agent task. Do not store secrets or personal files in the repository.

## Definition of done

- A user-approved local project with a normal multifile USD is discoverable, importable and inspectable through an
  actual MCP agent, and an agent can place an object and show a real screenshot without raw Python.
- The selected existing/disposable session is verified before mutation, UR5e tools and other sessions remain
  functional, no source asset is rewritten, collisions are explicit, and failures report partial effects honestly.
- Trusted and untrusted workflows are clearly distinguished; the isolated path passes enforcement and negative
  tests before it is enabled, and no untrusted loader silently falls back to an unrestricted Kit.
- The remote no-shared-mount workflow is authenticated and actually tested, and `.usdz` handling passes its archive
  gates; otherwise the affected path remains disabled and this end-to-end goal is recorded as incomplete.
  Unsupported dependency types and versions are explicit.
- CI and the real headless/windowed checks pass with linked evidence and screenshots, the independent agent
  demonstration passes, and the tool/setup/compatibility docs describe the final behavior. A green CPU-only CI
  result or a screenshot by itself is insufficient.
