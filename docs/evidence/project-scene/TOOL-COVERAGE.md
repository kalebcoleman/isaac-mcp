# Dedicated tool live coverage

The table preserves original call indexes from the historical release bundle. Updated full-suite
coverage on all five installed releases is in [run summaries](RUNS.md); each run records its
product revision. The current adapter has 217-capture stress runs per release, actual runtime
body velocities/mass, tested USDZ import and scoped Newton adapter tests. See [evidence](README.md)
for failures, final-change retests and limitations. Archives moved to the draft release; paths
below identify members of `historical-evidence-redacted.tar.gz`.


All 37 new tools below were exercised through real MCP on Isaac Sim 6.2.0.0 at implementation `f32876520ebaee97ef56c5c4422f84fb833bd8f8`. Call numbers are one-based positions in the linked raw JSON array. This covers the stated cases, not every argument or all fork behavior. [Run index](README.md) records independent use, failures, version limits and missing acceptance gates.

| Registered tool | Actual calls | Intended behavior observed and limits |
|---|---|---|
| `assets__roots` | core: 2 (`historical-evidence/runs/owned62-core.json`) | Approved fixture/Agilebot IDs, exact configured roots, disabled profiles and no sandbox claim returned. |
| `assets__scan` | core: 3, 4 refusal (`historical-evidence/runs/owned62-core.json`) | Actual multifile filenames found; unknown root refused. Fresh filesystem scan, not parsed kind metadata. |
| `assets__info` | core: 9 (`historical-evidence/runs/owned62-core.json`); equivalents: 20 (`historical-evidence/runs/owned62-equivalents.json`) | Root and transitive layers/textures reported for original fixture and real robot; source/snapshot hashes recorded. |
| `assets__import_usd` | core: 10, 11 refusal (`historical-evidence/runs/owned62-core.json`); equivalents: 21 (`historical-evidence/runs/owned62-equivalents.json`) | Both projects compose under new wrappers; conflict refused, rotated-parent world pose read back; independent source hashes unchanged. |
| `sim__stage__info` | core: 1 (`historical-evidence/runs/owned62-core.json`); equivalents: 1, 23, 25 (`historical-evidence/runs/owned62-equivalents.json`); clear: 1 (`historical-evidence/runs/owned62-clear.json`) | PID/incarnation/stage, units, timeline and count read; stale tokens refused; multi-instance targets distinguished. |
| `sim__stage__tree` | core: 5, 16 (`historical-evidence/runs/owned62-core.json`); clear: 4 (`historical-evidence/runs/owned62-clear.json`) | Composed scene enumerated; limit=1 returns truncation. Final protected-clear readback verifies remaining content. |
| `sim__prim__get` | core: 7 refusal, 13, 20, 26, 32 refusal (`historical-evidence/runs/owned62-core.json`); equivalents: 7 refusal, 34, 42 (`historical-evidence/runs/owned62-equivalents.json`) | Local/world pose and schemas read from USD, compared independently with runtime body samples. |
| `sim__prim__find` | core: 15 (`historical-evidence/runs/owned62-core.json`) | Expected imported robot descendant paths returned by bounded literal search. |
| `sim__prim__create` | core: 6, 8, 18, 19, 31 refusal (`historical-evidence/runs/owned62-core.json`); equivalents: 3, 4 (`historical-evidence/runs/owned62-equivalents.json`) | Xform, PhysicsScene, visual cube, static collider and dynamic rigid cube created and read back; fall/rest verified. |
| `sim__prim__set_transform` | core: 12, 22 refusal (`historical-evidence/runs/owned62-core.json`) | World pose beneath rotated parent matches [0.5,0,0]; active physics edit refused. No runtime teleport claim. |
| `sim__physics__inspect` | core: 14 (`historical-evidence/runs/owned62-core.json`) | Authored fixture/robot bodies, joints and articulation roots reported; no implied runtime initialization. |
| `sim__body__state` | core: 23, 25 (`historical-evidence/runs/owned62-core.json`) | PhysX body drops and rests; runtime and USD positions match; stopped/backend guards tested separately. |
| `sim__timeline__control` | core: 21, 24, 28 (`historical-evidence/runs/owned62-core.json`); equivalents: 28, 29, 38, 40, 41, 43 (`historical-evidence/runs/owned62-equivalents.json`) | Play/Pause states observed, physics advances, Stop restores spawn pose and previous edit target. |
| `sim__stage__save` | core: 27, 29, 30 refusal (`historical-evidence/runs/owned62-core.json`); equivalents: 44 (`historical-evidence/runs/owned62-equivalents.json`) | Separate paused/stopped files produced without stage switch/overwrite; independent saved resting state reopened in new Kit. |
| `sim__viewport__frame` | core: 17 (`historical-evidence/runs/owned62-core.json`); equivalents: 27 (`historical-evidence/runs/owned62-equivalents.json`) | Actual selected app view changes to requested objects, verified by independent window screenshots. |
| `assets__search` | equivalents: 2 (`historical-evidence/runs/owned62-equivalents.json`) | Actual robot filenames from approved roots found. No network/semantic search tested or implemented. |
| `sim__prim__delete` | equivalents: 6, 45 refusal (`historical-evidence/runs/owned62-equivalents.json`); clear: 2 refusal (`historical-evidence/runs/owned62-clear.json`) | Cloned owned prim removed, subsequent read is NOT_FOUND; owned sensor teardown/delete and recreate now tested. |
| `sim__prim__clone` | equivalents: 5 (`historical-evidence/runs/owned62-equivalents.json`) | New leaf Cube and transform read back; subsequently deleted. Arbitrary subtree clone absent. |
| `sim__stage__clear_owned` | clear: 3 (`historical-evidence/runs/owned62-clear.json`) | Removed/refused lists agree with subsequent stage tree; protected articulated projects remain; current sensor cleanup is tested separately. |
| `sim__light__create` | equivalents: 8 (`historical-evidence/runs/owned62-equivalents.json`) | Authored intensity/color read back; actual lighting visible in PNG and app. Not radiometric calibration. |
| `sim__light__modify` | equivalents: 9 (`historical-evidence/runs/owned62-equivalents.json`) | Intensity changed from 700 to 1000 and color read back; photometric output not calibrated. |
| `sim__material__create` | equivalents: 10, 12 (`historical-evidence/runs/owned62-equivalents.json`) | PBR values and physics material values authored/read; rendered color visible. Friction/restitution coefficients not dynamically measured. |
| `sim__material__apply` | equivalents: 11, 13 (`historical-evidence/runs/owned62-equivalents.json`) | Material binding/purpose reported and verified in CPU USD; real camera shows colored geometry. No coefficient-performance claim. |
| `sim__physics__configure` | equivalents: 14 (`historical-evidence/runs/owned62-equivalents.json`) | Gravity configured, 1/120 s actual step rate returned; measured 120-step delta=1 s and cube motion verify effects. |
| `sim__joints__inspect` | equivalents: 15 (`historical-evidence/runs/owned62-equivalents.json`) | Actual authored fixed/revolute limits/drive data read; units distinguish USD degrees from runtime radians. |
| `sim__joints__drive` | equivalents: 16, 17 refusal (`historical-evidence/runs/owned62-equivalents.json`) | 0.2-rad request authors 11.459°; runtime reaches about +0.2 rad; out-of-limit request refused. |
| `sim__joints__state` | equivalents: 30 (`historical-evidence/runs/owned62-equivalents.json`) | PhysX tensor DOFs and velocities read for fixture and real six-DOF robot; no drive-target fallback. |
| `sim__simulation__step` | equivalents: 33 (`historical-evidence/runs/owned62-equivalents.json`) | 120-step measured delta matches dt; body and articulation observations change as intended; independent 10/60/120 sequences and stable rest checked. |
| `sim__camera__create` | equivalents: 18 (`historical-evidence/runs/owned62-equivalents.json`) | Owned RTX camera produces actual images; current owned-CPU adapter exercised on both distinct 6.0.0 builds. |
| `sim__camera__capture` | equivalents: 35 (`historical-evidence/runs/owned62-equivalents.json`) | PNG dimensions/hash/pixels verified, six repeated frames show automatic descent/rest; latest-render semantics, not exact-step synchronization. |
| `sim__lidar__create` | equivalents: 19 (`historical-evidence/runs/owned62-equivalents.json`) | Owned RTX LiDAR initializes with ordinary rendered frames and produces populated GMO returns. |
| `sim__lidar__read` | equivalents: 36 (`historical-evidence/runs/owned62-equivalents.json`) | Finite bounded points include the authored wall at ~2.5 m; coordinate frame labeled. No hardware calibration or long-lived stability claim. |
| `sim__logs__tail` | equivalents: 37 (`historical-evidence/runs/owned62-equivalents.json`) | Bounded warnings from selected Kit log returned, not arbitrary host files/stdout. Full process logs retained separately. |
| `sim__controller__create` | equivalents: 22 (`historical-evidence/runs/owned62-equivalents.json`) | Opt-in graph created; subsequent updated code actually executes on playback, moving the marker. Fixed topology only. |
| `sim__controller__update` | equivalents: 26, 39 (`historical-evidence/runs/owned62-equivalents.json`) | Owned inline code replaced/recompiled while stopped; after playback marker read changes to Z=2. General file/module reload absent. |
| `sim__joints__command` | equivalents: 31, 32 refusal (`historical-evidence/runs/owned62-equivalents.json`) | Full ordered [-0.2] target acknowledged as not-yet-achieved; after 120 steps observed value is about -0.2 rad; bad limit refused. |
| `assets__import_urdf` | equivalents: 24 (`historical-evidence/runs/owned62-equivalents.json`) | Minimal original URDF converted/imported; active root and session/stage identity preserved. Complex mesh/package URDFs are not proven. |
