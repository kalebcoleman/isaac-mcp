# Demo / full-system test plan

Goal: prove the stack works end to end on both machines (`asu` = lab RTX PRO 6000, `home5080` = home RTX 5080) and
collect evidence (screenshots, short videos, numbers) for a slide presentation of the foundation work.

Evidence conventions
- Raw output of a run: `demo-out/<machine>/` (git-ignored): `report.json`, `report.md`, `logs/`, `shots/`, `videos/`, `data/`.
- Committed, compressed media: `docs/demo/media/<machine>/` (PNG <= 1.2 MB each, MP4 <= 10 MB each, 720p) made by `scripts/demo/make_media.sh`.
- File names: `<scenario>-<nn>-<slug>.png|mp4`, e.g. `S2-01-workstation-loaded.png`.
- Machine ids: `asu`, `home5080`.
- A step never aborts the whole run: record `ok`, seconds, a one-line note and the artifact paths, then continue.

Scenarios (runner: `scripts/demo/run_demo.py`)

| Id | Scenario | Evidence | Pass criteria |
|---|---|---|---|
| S0 | Machine snapshot | `data/machine.json`: hostname, OS, kernel, GPU, driver, VRAM, Python, isaacsim/isaaclab/torch versions, git HEADs of isaac-mcp and the twin repo, uv version, DISPLAY | file written, versions are 6.2.0.0 / 3.0.0 |
| S1 | Health check | `logs/check_machine.txt` | ends with "Machine check passed." |
| S2 | Isaac Sim twin, windowed | boot time, tool list (`data/tools.json`: total, per-namespace counts), `shots/S2-01-workstation-loaded.png`, `shots/S2-02-objects-playing.png` | 30 bridge tools (23 `ur5e_dt.*`), quick_start status success, window screenshot shows the UR5e on the Vention workstation |
| S3 | ROS TF | `data/tf.json` from `scripts/tf_capture.py` (5 s) | `objects_poses_sim` has >= 5 frames and `tf_cam` >= 1 frame |
| S4 | Motion video | `videos/S4-01-joint-sweep.mp4` (10 s clean viewport via `ur5e_dt.start_recording`/`stop_recording` while joints move) + 2 stills | video file > 50 KB, robot visibly in different poses between first and last frame (compare stills) |
| S5 | Isaac Lab tools | `data/lab_install.json`, `data/lab_envs.json` (filter `ur5e` and `reach`) | isaaclab 3.0.0, isaacsim 6.2.0.0, `UR5e-Cell-Reach-Direct-v0` listed |
| S6 | Guards | `data/guard.json`: refusal text when a 2nd job starts, refusal when a Kit app runs, and release after cancel | all three behave as in `docs/ISAACLAB-TOOLS.md` |
| S7 | Training | N-minute (default 5) `UR5e-Cell-Reach-Direct-v0` run, 1024 envs: fps, peak VRAM, `shots/S7-01-reward-curve.png`, `shots/S7-02-success-curve.png` (from TensorBoard via `scripts/demo/plot_curves.py`) | job succeeded, reward rises |
| S8 | Windowed policy play + visual feedback | `shots/S8-01-play-window.png`, `videos/S8-02-play.mp4` (~15 s), `data/window.json` (job status reporting `window`) | window appears, status reports it, screenshot shows the arm and target |
| S9 | Screen tools | `shots/S9-01-covered-window.png`: capture of the Isaac window while another window covers it | image shows Isaac, not the covering window |

After a run: `scripts/demo/make_media.sh demo-out/<machine> docs/demo/media/<machine>` and commit ONLY `docs/demo/media/<machine>/` plus
`docs/demo/results/<machine>.md` (copy of `report.md`). Never use `git add -A` in this repo.

Rules for everyone running this
- One GPU job at a time; no stray Kit/train processes (kill by PID with `kill -9`, never `pkill -f` with a pattern that can match your own shell).
- `OMNI_KIT_ACCEPT_EULA=YES` must be set before anything imports isaacsim.
- Isaac Sim ignores SIGTERM: stop it with `kill -9 <pid>`.
- Do not edit tracked files outside `scripts/demo/` and `docs/demo/`. Report problems you find in the product code instead of fixing them.
