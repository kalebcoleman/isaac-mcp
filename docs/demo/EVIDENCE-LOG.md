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

## Pending
- Home 5080 run (agent publishing `docs/demo/media/home5080/`), then side-by-side comparison.
- Control experiments on the real twin (step response, tracking, gain sweep, IK): `demo-out/asu/control/`.
- MPC figures, and the comparison of baseline / resolved-rate / MPC / PPO on the same targets.
- A better S8 clip (see above) and a cropped S2 hero image without the UI panel.
