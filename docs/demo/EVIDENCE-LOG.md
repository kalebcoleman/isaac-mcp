# Evidence log (screenshots, videos, figures)

Living log: every image or clip used for the presentation, what it actually shows, whether a human/agent looked at it, and caveats.
"Eyeballed" = the file itself was opened and viewed, not just its metadata. Machine `asu` = lab RTX PRO 6000 (driver 595.99.02);
`home5080` = home RTX 5080 (driver 580.178.04). Raw run output lives in `demo-out/<machine>/` (git-ignored); published copies are compressed
in `docs/demo/media/<machine>/` (index in `INDEX.md`). Run reports: `docs/demo/results/<machine>.md`.

## ASU run, 2026-10-30 (runner commit 6b863f6, 10/10 scenarios pass, 500 s wall, 5 min training)

| File (`docs/demo/media/asu/`) | Scenario | What it shows (as seen) | Eyeballed | Slide idea | Caveat |
|---|---|---|---|---|---|
| `S2-01-workstation-loaded.png` | S2 | Isaac Sim 6.2 window (RTX Real-Time 2.0), UR5e on the Vention bench, the "UR5e Digital Twin" control panel, stage tree | yes | "The twin loads from one MCP call" | UI panel covers the viewport; a toast ("Mismatched units") overlaps. Prefer the clean frames below for hero images. |
| `S2-02-objects-playing.png` | S2 | Same window after `add_objects` + `play_scene`: coloured fmb1 blocks on the bench | yes | Scene after tool calls | status bar shows a harmless "client could not be answered" bridge message from a one-shot client; panel covers the viewport |
| `S4-clean-t1s/t5s/t9s/t13s.png` | S4 | Clean 1280x720 viewport from the Kit recording (no UI): UR5e on the bench with blocks; arm in visibly different poses (t=5 s vs t=13 s: wrist and elbow differ) | t5, t13 yes; t1, t9 no | **Hero images**, motion sequence | black studio background |
| `S4-01-joint-sweep.mp4` | S4 | 14.5 s clean-viewport joint sweep, 720p, 0.24 MB after compression | frames only (not played) | Short video of commanded motion | |
| `S7-01-reward-curve.png` | S7 | 5-minute `UR5e-Cell-Reach-Direct-v0` run at 1024 envs: reward rises from about -125 to positive; smoothed + raw | yes (2-min test run version; 5-min version not yet viewed) | "It learns" | stepwise raw line because `rewards/iter` is logged sparsely |
| `S7-02-success-curve.png` | S7 | Success rate rises 0 -> ~0.54 over ~260 iterations with a sawtooth in the raw line | yes | success curve | the sawtooth is real: environments reset in sync (episode 5 s), so per-iteration success alternates; use the smoothed line |
| `S7-03-distance-curve.png`, `S7-04-fps.png` | S7 | end-effector distance to goal, throughput (~23k fps) | no | backup slides | not yet viewed |
| `S8-01-play-window.png` | S8 | "Isaac Lab 3.0.0" window, "Physics: PhysX" label, UR5e on the bench, sky-blue background, trained policy loaded | yes | windowed policy play, visual-feedback loop (job status reports the window id) | one environment's camera; no obvious target marker visible |
| `S8-02-play.mp4` | S8 | 15 s window recording of the policy | frames at t=1/7/13 s viewed numerically (1-1.6 % pixels change) and t=13 s viewed | **weak**: arm nearly static | improve: follow-camera on the arm, 30 s, show a reset to a new target |
| `S9-00-uncovered.png`, `S9-01-covered-window.png` | S9 | Isaac window captured by window id while another window sits above it in the stacking order; capture shows Isaac's own pixels | S9-01 yes, S9-00 no | "capture works even when covered" | proven by X stacking order + pixel comparison; |
| (not published) `S9-02-covering-window.png` | S9 | the covering window = the presenter's chat UI | yes | **do not publish** | contains chat text, hostname and agent list (privacy) |

Figures from the controller work (CPU-only, offline, `demo-out/resolved-rate/`, not yet copied to media):
- `resolved_rate_singularity.png` (eyeballed): plain pseudo-inverse near the stretched-elbow singularity commands 146 rad/s (487x the 0.3 rad/s limit); damped least squares 1.45 rad/s; damping + velocity cap 0.30 rad/s.
- `resolved_rate_reach.png` (eyeballed): position error below 5 mm at 1.27 s, joint speeds capped at 0.3 rad/s for 33 % of the run.
- `resolved_rate_circle.png`: not yet viewed.

## Real-twin control experiments, 2026-10-30 10:40 (second run, after two fixes)

Command: `OMNI_KIT_ACCEPT_EULA=YES .venv/bin/python scripts/demo/control_experiments.py --port 8766 --out demo-out/asu/control`
against a windowed twin (`./run.sh --port 8766` + `ur5e_dt.quick_start_with_workstation`). Raw CSV/PNG/summary in
`demo-out/asu/control/` (`step-20261030-104053.*`, etc.); the 10:17 run is kept as `summary-*.json` next to it.

The first pass measured two things that looked like controller faults and were not. Both are fixed now:

- **IK missed by 308 mm with a 0.00 mm solver residual.** `LulaKinematicsSolver.set_robot_base_pose()` takes a
  `(w, x, y, z)` quaternion; the experiment handed it `(x, y, z, w)`. The twin's `base_link` is rotated 180 deg about z,
  so Lula solved in an x/z-mirrored world and the "correct" joint solution drove the real arm to the mirror image.
  Passing wxyz: final EE error **0.00 mm**, settle 0.41 s.
- **Elbow step stalled at 0.050 rad of 0.300 with an 83 % "steady-state error".** The RG2 fingertips sit 27 mm above the
  Vention top at the home pose (`/World/RG2_Gripper` bbox min z 0.0273 vs bench max z 0.0000), so the step pushed the tool
  into the bench and the "error" was contact. The experiments now lift the elbow 0.5 rad through `/joint_states` first
  (`--pre-lift`, measured and reported in `summary.json.prepose`): rise 0.216 s, overshoot 0.0 %, settle 0.523 s, ss err 0.0000 rad.
- **The drive the solver ran was not the drive the twin configured** (found while chasing the two above). `load_ur5e()`
  wrote `stiffness=100` into the USD drive but PhysX had cached the asset's authored gains (586148 N*m/rad on the elbow,
  i.e. ~100x), and a same-value write notifies nobody. The extension now pushes the configured values with `set_gains()`
  and verifies them on the console. Effect on the numbers: 0.5 Hz tracking lag went 29.2 -> 40.9 deg because the
  previous figure was measured with the stiff-by-accident drive. Every control figure is now labelled with the gains
  PhysX actually used (footer line); the unit trap is written up in `docs/demo/CONTROL-EXPLAINED.md` section 2.4.

| File (`docs/demo/media/asu/`) | Scenario | What it shows (as seen) | Eyeballed | Slide idea | Caveat |
|---|---|---|---|---|---|
| `S10-01-step-response.png` | control | elbow step -0.500 -> -0.200 rad: clean first-order rise, 0.216 s, no overshoot, settles 0.523 s, zero steady-state error | yes | "the twin's joint controller is a PD drive, and it behaves like one" | the plotted peak time (1.54 s) is just the end of the plateau, ignore it |
| `S10-02-sine-tracking.png` | control | +/-0.25 rad sine at 0.2 and 0.5 Hz: smooth tracking, gain 0.995/0.967, lag 17.5/40.9 deg, error trace below | yes | "tracking bandwidth: 17 deg at 0.2 Hz, 41 deg at 0.5 Hz" | lag is dominated by the 0.14 s dead time of the 100 Hz publish path, not by the drive alone |
| `S10-03-gain-sweep.png` | control | six stiffness/damping pairs (50..400 / 5..20) normalised: settling scales with damping/stiffness as a first-order lag should | yes | "gains are a real knob, and the sweep is monotone" | settle-time column is partly under the legend |
| `S10-04-lula-ik-move.png` | control | 80 mm Cartesian goal from Lula IK executed over `/joint_states`: EE error falls 80 -> 0.00 mm in 0.41 s | yes | "IK + the command path land on the target" | goal is a fixed +x offset, not a random reachable set |
| `S10-05-window-prelift.png` | control | the live Isaac 6.2 window at the pre-lift pose, UR5e on the bench, control panel open | yes | "this ran in a visible window, not headless" | panel covers most of the viewport (toast gone); use S4 clean frames for hero images |

## Pending
- Home 5080 run (agent publishing `docs/demo/media/home5080/`), then side-by-side comparison.
- MPC figures, and the comparison of baseline / resolved-rate / MPC / PPO on the same targets.
- A better S8 clip (see above) and a cropped S2 hero image without the UI panel.
