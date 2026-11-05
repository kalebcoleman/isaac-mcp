# `sim__arm__ik` and `sim__arm__fk` on five robots, Isaac Sim 6.2 (2026-11-04)

Built by Claude Sonnet 5.5 from a pick-and-place wishlist item (a Gemini 3.8 Flash tester spent about 12 calls per reach
finding joint angles by trial and error). One run, headless, PhysX, an RTX 5080, NVIDIA's public Franka loaded by name with
`sim__quick_start`, played and then paused. The IK tool is solve-only; the arm was then moved with the existing
`sim__joints__move`. No agent chose these targets: they are fixed offsets from the hand's starting pose, written for the check.

## Self-check

Before solving, the tool compares its forward-kinematics model (built only from the robot's USD joints) with the hand pose
PhysX reports. On the Franka the two agreed to 0.0005 mm in position and 0.0005 milliradians in orientation, so the model
reads the joint frames, axes and the chain from the base to `panda_hand` correctly. A mismatch beyond 2 cm or 0.05 rad makes
the tool refuse (`IK_MODEL_MISMATCH`) instead of answering.

## Four reaches

| Target (offset from the starting hand pose) | Solver result | Hand after `sim__joints__move`, measured against the target |
|---|---|---|
| 8 cm forward, same orientation | reached, 0.4 mm, 3 iterations | 1.8 mm off |
| 15 cm left and 10 cm down, same orientation | reached, 0.1 mm, 7 iterations | 1.8 mm off |
| 5 cm back, 20 cm right, 5 cm up, position only | reached, 0.6 mm, 5 iterations | 2.1 mm off |
| 2 m forward (out of reach) | `reached=false`, 1.53 m short after 1,800 iterations over 9 starts | not moved |

Each reach took two tool calls (one `sim__arm__ik`, one `sim__joints__move`). The 1.8 to 2.1 mm gap after moving is the
arm's position drive settling, not the solver, which was within 0.6 mm of the target. Two of the moves reported
`settled=false` against a 0.002 rad tolerance.

## Forward kinematics, and the two together

`sim__arm__fk` answers "where would the hand be for these joint values". Final-code run on the same Franka:

- With the timeline stopped and no joints given, it returned the zero-joint pose (hand at 0.088, 0.000, 0.926: the arm
  straight up), with a note that the base pose came from the authored USD.
- Running, with the joints left out, it returned the hand's current position and the self-check against physics agreed to
  0.0005 mm.
- An IK answer for a target 8 cm forward, 10 cm left and 8 cm down (0.03 mm solver error) was passed back through
  `sim__arm__fk`: the predicted hand position was within 0.1 mm of the target. After `sim__joints__move`, the real hand
  was 1.4 mm from that prediction (the drive settling).

So an agent can plan with FK and IK without moving the arm, and moves it with one `sim__joints__move`.

## Five different robots (final code)

The same tools were run, in one throwaway Isaac Sim 6.2 (headless, PhysX), on robots with very different chains. Targets were
**built to be reachable**: the forward-kinematics pose of slightly different joint values inside the limits, asked for from
the current pose, then moved to with `sim__joints__move` and measured against physics. Hand errors are the real body pose
after the move, so they include the arm's own drive settling.

| Robot and chain | Self-check against physics | Trials | Solver error | Real error after the move |
|---|---|---|---|---|
| Franka Panda, 7 joints, full pose | 0.0005 mm | 3 of 3 reached | 0.03 to 1.0 mm | 0.7 to 1.6 mm, 0.1 to 0.3 deg |
| UR5e, 6 joints, full pose (raised off the ground first) | 0.0003 mm | 3 of 3 | 0.2 to 0.9 mm | 0.3 to 0.9 mm, 0.02 to 0.07 deg |
| Unitree G1 left arm from the pelvis, 10 joints, full pose | 0.0001 mm | 3 of 3 | 0.04 to 0.8 mm | 2.7 to 3.2 mm, 1.2 to 1.3 deg |
| Same arm, position only | same | 3 of 3 | 0.02 to 0.6 mm | 2.3 to 4.1 mm |
| SO-ARM100 (imported from a URDF), 5 joints, full pose, targets inside the limits | 0.00005 mm | 12 of 12, one attempt each | 0.06 to 1.0 mm | 1.8 to 2.9 mm, 0.8 to 1.2 deg |
| Barkour quadruped leg, 3 joints, **floating base**, position only | 0.0001 mm | 3 of 3 | 0.1 to 0.8 mm | 1.6 to 10.6 mm (the base moves as the leg does) |

The G1 was also solved from `torso_link` as the base (7 joints) and reached a 6 cm forward target to 0.2 mm in the solver,
4.4 mm real. The self-check passed on all of them, including the floating base.

## What the cases found, and what changed

Eight first-round cases did not reach. Each was checked, and four things changed the tool or its docs:

- **Targets I built outside the joint limits (my test, not the tool).** The SO-ARM100 sits exactly at two of its limits at the
  zero pose, so random nudges put the "reachable" target outside them; `sim__arm__fk` warned about it. With targets inside
  the limits it reached 12 of 12.
- **A link's own origin cannot be moved by its own last joint.** "Foot forward" on the Barkour's `lower_leg_1` failed because
  that link's origin is its knee. The result now says which joints do not move the controlled point (`why_not_reached`), and
  a new `offset` argument places a point beyond the origin (a fingertip or foot tip) in the link's frame.
- **A pose can be reachable and still blocked.** A UR5e target 2.5 cm below the ground plane solved (0.9 mm) but the arm
  stopped 71 mm short and `sim__joints__move` said `settled=false`, three times in a row. The tool has no collision check
  and says so; reachable does not mean collision-free.
- **Fewer than six joints cannot hold any orientation.** The tool says so when it fails with fewer than six movable joints.
- **The solver stalled a few millimetres short on a 5-joint arm.** Replaced the fixed damping with adaptive damping
  (Levenberg-Marquardt) and raised the random restarts from 8 to 16. A twenty-pose random test caught one more local
  minimum that 16 restarts solves in 0.5 s.
- **Not explained:** a Franka solved from `panda_link2` as the base (5 joints) did not reach a 6 cm forward position target
  (49.9 mm short). That may be a real workspace limit of the shortened chain, but I did not verify it.
- **UR5e at its zero pose** is fully stretched out and singular: a 10 cm forward target there is beyond reach, which the tool
  reports as not reached.

## Code review, round 1 (Claude Sonnet 5.5, medium effort)

A read-only review of `ik.py` and the arm code found no error in the core joint convention but ten ways to get a wrong or
unusable answer. Each was fixed or answered:

- **Self-check refused instead of skipping** when the end-effector link is not a body physics reports; now skipped with a note.
- **Forward kinematics swallowed every simulation error**, so an invalid or quarantined state looked like "not running"; now
  only "not running" falls back to the authored pose.
- **No time bound:** an unreachable target on a 16-joint chain took 5.8 s. Added a 3 s budget (`timed_out` in the result),
  cheaper rigid inverses, and a per-axis step cap.
- **Continuous joints** could be returned a whole turn away from the current angle; they are now turned to the nearest
  equivalent angle where the limits allow.
- **The self-check cannot reveal a flipped axis at the all-zero pose;** the result now says so when every joint is at zero.
  An unknown axis token and reversed limits are refused.
- **Duplicate joint names** in one chain are refused; **a scaled base** no longer makes a non-rigid base pose; **the last
  accepted solver step** is now judged; orientation is weighted against position on non-metre stages; a truncated
  articulation scan is named in the "no chain" refusal.

Tests went from 18 to 32 and now include a hand-composed forward-kinematics check with rotated joint frames, offset child
frames, an X-axis revolute and a Z-axis prismatic joint (the earlier straight arm could not catch a swapped frame), and
mocked-runtime tests for the self-check, a mismatch, the base frame, a missing degree of freedom and duplicate names.
Each fix was checked by putting its bug back and watching a test fail; two first attempts at that (a poor mutation, and a
missing test that `solve` applies the wrap-around) were redone. The live trials above were run again on the final code with
the same results.

Not changed: mimic joints are still treated as independent (the contract now says so).

## Code review, round 2 (delta only)

A second review of the fixes found three real defects and some test gaps, all fixed:

- `timed_out` could be reported when an earlier attempt had succeeded; it is now set only when nothing succeeded before the clock ran out.
- Candidates were ranked by raw joint distance before whole turns were removed, so "nearest the current pose" could pick the wrong
  branch on a continuous joint; each candidate is now wrapped before it is ranked.
- A link beyond a truncated articulation scan was reported as "not a body of this articulation"; the refusal now says the scan
  stopped at its limit.
- Smaller: the solver returns the last accepted step instead of the best-so-far from one step earlier.

Five more tests (37 in all): a success followed by a timeout, ranking after wrapping, a centimetre stage built from the same
arm (ten random reachable poses to a millimetre and a hundredth of a radian), a 160 cm rail crossed within the iteration
budget, and the truncated-scan message. Four of the five bugs were put back and each made a test fail. **Not caught:** removing the
unit scaling of the rotation rows on a non-metre stage. A reachable target converges either way, and only a contrived
over-constrained case would show the weighting, so this is a known test gap, not a fixed one. The live trials above were run again
on this code with the same results.

## What this does not show

- One session on six robots, one run each. The unit tests cover a synthetic arm with revolute and prismatic joints, reversed
  joints, limits, unreachable targets, an unsupported joint, tip offsets and twenty random reachable poses. No Newton run,
  and no robot whose chain includes a joint type other than revolute or prismatic (those are refused, not solved).
- A solution only: no collision check, no path between poses, and it picks the solution nearest the current pose among those
  it finds. A 7-joint arm has many.
- Orientation tolerance 0.01 rad and position tolerance 1 mm are the defaults; the first two reaches used orientation.
