# Current PhysX body, joint and step qualification

Exact combined source **`0163b3f07e6b9d3f51006f8845156f664429e580`**, tree `ed22076e3816591c7027584455576e9b73661a55`, passed this targeted regression on all five installed package builds in headless and windowed modes. Later integration changes are documentation only; these runs retain their source identity. **PhysX is the release target.** Newton evidence remains experimental, with no further Newton qualification planned under the user's 2026-11-02 direction.

**445 core MCP calls, including 100 expected error results, and 230 assertions** cover the current `sim__body__state`, `sim__joints__state` and `sim__simulation__step` paths. Each case has 23 assertions, including targeting, refusals, finite observations, source preservation and lifecycle checks; this is not 230 independent motion experiments or a new 99-tool pass. No product code changed.

The [compact](RECENT-PHYSX-POSITIVE-0163-20261102.json) records exact arguments/results through hashed references, package/Kit identities, ports/PIDs, full source-module comparisons, numeric observations and the timestamped Console reviews. The [draft archive](RELEASE-ASSETS.md) contains the real MCP envelopes, logs, original-hash manifest, all commands and reproduction scripts. Draft access requires repository permission; nothing was publicly published.

## Measured effects

- An original CC0 multifile articulation and a falling cube were imported/created through named tools. The cube fell onto the known floor; the driven elbow reached +0.2 and -0.2 radians, with a separately observed arm world displacement. Runtime mass, velocities and quaternion values were finite, with explicit PhysX sources.
- Six successful step requests per case (1, 12, 60, 120, 120 and 12) advanced the measured physics clock at 120 Hz: 325 steps per case, 3,250 across the ten cases. These are simulation intervals, not wall-clock durations or rendered frames.
- Stopped/playing, invalid bounds, missing path, stale target and joint-limit requests refused. The paused clock stayed unchanged across the invalid preflights. Stop → Play → Pause restored usable finite physics for the last 12-step observation.
- The original robot, material, texture and license bytes were unchanged. Stage/session identity was retained. This is not a whole-root-layer preservation assertion: native physics writeback and disposable scene authoring occurred.

| Package build | H / W core calls | H / W assertions | Final native warnings H / W | Window Console warnings / errors |
|---|---:|---:|---:|---:|
| 6.0.0.0 | 44 / 45 | 23 / 23 | 8 / 11 | 13 / 0 |
| 6.0.0.1 | 44 / 45 | 23 / 23 | 9 / 12 | 14 / 0 |
| 6.0.1.0 | 44 / 45 | 23 / 23 | 10 / 9 | 11 / 0 |
| 6.1.0.0 | 44 / 45 | 23 / 23 | 6 / 9 | 9 / 0 |
| 6.2.0.0 | 44 / 45 | 23 / 23 | 12 / 9 | 9 / 0 |

All ten final native logs contain zero lines in the tracked error, traceback, cycle, simulation-time, force-callback and nested-task categories. That is a bounded log observation, not proof of no possible defect. Actual application windows were selected by PID/window ID and inspected before/after; the Console tab was opened in all five windowed cases. Three stills per window were reviewed, with wide framing and no video. Physical effects are established by numeric runtime results, not those stills. Images remain private; hashes and review records are distributed.

UI counters and final native-line counts are different observations. Startup/USD and bridge client-close warnings remain, including BrokenPipe/Bad-file-descriptor warnings after successful client exchanges. No warning was suppressed or called generally harmless. All ten owned processes exited normally through managed SIGINT and were confirmed absent; no unrelated process was stopped.

## Preserved test preparation limits

- The first 6.2 headless client stopped before Play because its test reader did not unwrap MCP's error prefix. The corrected reader recreated the stopped stage in the same owned process. The correct original `PHYSICS_NOT_READY` response is retained; the other qualified cells started fresh processes.
- A 6.1 launch refused a port still in TCP `TIME_WAIT`; a 6.0.0 launch refused ROS domain 232 outside the configured 0–231 range. Neither started Kit. The corrected private driver used distinct free ports and valid domains, without changing a guard.
- The first 6.2 window Console click omitted required dimensions and refused before input. The corrected click used the observed 1440×900 dimensions and selected PID.
- These are prepared older environments and the previously recorded locked cached 6.2 environment, with an upload-disabled diagnostic launcher and faulthandler. All interpreters report Python 3.12.3 / NumPy 2.3.1; host MCP SDK is recorded separately from optional distributions in Kit. No clean-machine/full-installer qualification is implied.

## Remaining review items

1. The original `20f` 6.0.1 windowed 108-step PhysX stall is still unresolved. This smaller fixture is not its reproduction or explanation. The next task is a finite, source-matched reproduction/recovery exercise; passing repeats must not be labeled a fix.
2. Latest `1f` camera clipping outputs were reopened with standalone OpenUSD; fresh-Kit reuse of those particular new files remains unqualified. Earlier native fresh-reopen results retain their own source and scope.
3. Fresh-user installation on the user's lab workstation, final combined-head checks and the user's code review remain separate. Prepared-environment, full-installer and Lab metadata/locked-replay limits stay explicit.
4. Existing Newton failures and bounded 6.2 candidate successes are experimental limitations, not further release-completion work. Isolated untrusted loading, authenticated remote upload and presets remain the agreed deferred work.

The [99-tool census](CURRENT-TOOL-RECONCILIATION.md) and its JSON snapshot keep their historical counts. This supplemental record closes only its then-open normal PhysX body/joint/step path gap. PR #16 remains draft and unmerged; there is no merge or publication authorization.
