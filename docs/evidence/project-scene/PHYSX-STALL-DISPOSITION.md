# Bounded PhysX stall assessment

**Not reproduced; original cause unresolved.** On 2026-11-02, two disposable
6.0.1.0 windowed PhysX processes replayed the original task on exact combined
`0163b3f07e6b9d3f51006f8845156f664429e580`. Both completed the previously stalled
108-step operation. This is bounded negative reproduction evidence, not a fix,
an explanation, or a guarantee against another native stall. No product code or
solver settings changed. PR #16 remains draft and unmerged.

The [compact record](RECENT-PHYSX-STALL-BOUNDED-0163-20261102.json) pins the calls,
processes, environment, source differences, visuals and cleanup. The
[draft attachments](RELEASE-ASSETS.md) contain the redacted harnesses, responses
and native logs; repository access is required. Source geometry and images remain
private. The original [20f failure record](RECENT-601W-20261102.json) is unchanged.

## What was matched

The original `20f2953` process had been alive for 751.480 seconds and had completed
earlier ROS, sensor, stage-replacement and broad tool workflows. The independent
task added Agilebot, an original multifile fixture, a falling sample, support,
light and camera to the existing UR5e/workstation scene. It started a selected-window
960-pixel, 10-fps recording, commanded both robots, completed 12 steps, read the
sample, then received no response to the next 108-step call. A Python stack
localized synchronous native simulation; it did not establish the cause or the
number of completed steps. Closing the client did not establish cancellation.

Both new attempts used the unchanged 35,363-byte checkpoint
(`5e886cac0a7b47f9cdff4cac8e152ac4c9591d819d399a4641b6f53096ea9ea9`)
and all 44 matching original asset/cache hashes. The old checkpoint has an
**untyped PhysicsScene over with authored 120 Hz**; the observed native rate was
**60 Hz** in the original and both replays. No rate override or checkpoint repair
was applied. The separately fixed checkpoint exporter is not an explanation of
this stall. Source-file preservation does not imply unchanged native in-memory
physics writeback or an identical solver state.

| Attempt | Process history / age at 108-step dispatch | Completed replay calls | Measured result |
|---|---|---:|---|
| A | Fresh process, original inspection/paused gaps; 259.600 s | 48 | 12 steps advanced 0.2 s; 108 advanced 1.8 s, returning in 0.375 s wall time |
| B | Two prior ROS Play/Pause/Stop cycles, generic camera and stage replacement; 751.665 s | 54 | 12 steps advanced 0.2 s; 108 advanced 1.8 s, returning in 0.372 s wall time |

Both reported finite runtime body/joint values, the sample resting at approximately
z=1.04 m, and joint targets within 0.001 rad. Wrist and gripper world poses moved.
Fresh camera output and recording completed. These effects, not a success string,
support the scoped replay result. The two successful continuations contain 30
script assertions, including identity and preservation checks, not 30 independent
motion experiments. Thirteen current host/runtime/catalog files differ from `20f`;
the smaller prior-history sequence cannot recreate its native allocation history.

Two separate harness setup failures are retained: A's first five calls stopped on
the ordinary `NO_STAGE` interval during asynchronous opening, before physics;
B's 25-call history sequence ended with `INVALID_FRAME` because its added diagnostic
camera omitted `frame="world"`. Corrected private harnesses continued the same
respective processes; B did not repeat its history cycles. There were two Kit
processes and 132 completed harness calls including these setup/history records.
No timed-out mutation was retried and no third native attempt was made.

## Observation and cleanup limits

Actual application Consoles showed A: 18 warnings / 0 errors and B: 28 / 0.
Final native logs separately contained 15 and 25 warning lines, with zero error,
traceback, cycle, simulation-time, force-callback or nested-task matches. Startup,
USD, cooking, bridge-client-close, CameraInfo, DLSS and texture-release notices
remain in the logs. These scoped observations do not classify every warning as
harmless. Seven stills were reviewed across the two runs; 13/14 video frames were
decoded and only the first/last of each were visually sampled. Framing crops the
original cell and the falling sample; physical state comes from runtime readback.

Both owned processes exited normally through managed SIGINT and were confirmed
absent. The full slot, including cleanup confirmation, took 1,143.29 seconds
(19.1 minutes), below the stated two-process / 35-minute ceiling. GPU usage returned
near its pre-run level. The original process had required forced cleanup after
SIGINT did not exit within 25 seconds; that historical cleanup is not recovery
of the unknown in-flight simulation state. No failure was injected in the new runs
solely to claim recovery validation.

## If an operation stops returning

1. Stop dependent mutations. A client deadline or closed socket does not cancel a
   synchronous native call, establish rollback, or make repeating it safe.
2. Preserve the request, dispatch time, selected stage/session/PID, output and logs.
   Use bounded read-only status and process/log observations. A registered diagnostic
   stack dump localizes execution; never send its signal to an arbitrary application.
3. For an owned managed session, use `sim__status`, then `sim__stop` with the recorded
   managed ID, `expected_pid` and `confirm_discard=true`. If that same process does
   not exit normally, explicit `force=true` is the last resort. Unsaved changes and
   unknown in-flight effects can be lost. Never stop unrelated processes or recordings.
4. Verify exit/resources before a fresh launch. Reopen a known saved input and verify
   the new endpoint, stage, backend, rate, finite state and a small bounded step.
   This restores a new working session, not the original solver state.

The bounded assessment is complete with an explicit residual PhysX risk. It does
not close the original failure as fixed. The user-led workstation installation
test and final combined review are separate; existing fresh-Kit camera-fixture,
prepared-install and Lab limitations remain documented. Newton stays experimental
and outside further release qualification. No merge or publication is authorized
by these results.
