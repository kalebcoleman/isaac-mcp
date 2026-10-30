# Demo run — asu (EN4232374L)

Started 2026-10-30 10:08:35, finished 2026-10-30 10:16:55 (499.5 s wall).
Port 8791, lab minutes 5.0, num_envs 1024, headless False.

**10 pass, 0 fail, 0 skipped.**

| Id | Scenario | Result | Seconds | Note |
|---|---|---|---|---|
| S0 | Machine snapshot | PASS | 1.3 |  |
| S1 | Health check | PASS | 3.1 |  |
| S2 | Isaac Sim twin, windowed | PASS | 28.2 |  |
| S3 | ROS TF | PASS | 7.5 |  |
| S4 | Motion video | PASS | 10.5 |  |
| S5 | Isaac Lab tools | PASS | 3.6 |  |
| S6 | Guards | PASS | 2.1 |  |
| S7 | Training | PASS | 402.3 |  |
| S8 | Windowed policy play + visual feedback | PASS | 38.6 |  |
| S9 | Screen tools | PASS | 1.2 |  |

## S0 — Machine snapshot: PASS

| Key | Value |
|---|---|
| hostname | EN4232374L |
| gpu | NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition |
| driver | 595.99.02 |
| vram_gb | 95.6 |
| python | 3.12.16 |
| isaacsim | 6.2.0.0 |
| isaaclab | 3.0.0 |
| torch | 2.11.0+cu130 |
| display | :1 |
| isaac_mcp_git | 6b863f6b |

| Check | Result |
|---|---|
| machine.json written | PASS |
| isaacsim 6.2.0.0 and isaaclab 3.0.0 | PASS — {"isaacsim": "6.2.0.0", "isaaclab": "3.0.0"} |

Artifacts: [machine.json](data/machine.json)


## S1 — Health check: PASS

| Key | Value |
|---|---|
| exit_code | 0 |
| last_line | Machine check passed. |
| fails | 0 |
| warns | 0 |

| Check | Result |
|---|---|
| ends with "Machine check passed." | PASS — {"last_line": "Machine check passed."} |
| exit code 0 | PASS — {"exit_code": 0} |

Artifacts: [check_machine.txt](logs/check_machine.txt)


## S2 — Isaac Sim twin, windowed: PASS

| Key | Value |
|---|---|
| boot_seconds | 8.8 |
| kit_ready | {"ready_in_s": 3.2, "stage": true, "app_ready": true, "prims": 11} |
| tools_total | 30 |
| ur5e_dt_tools | 23 |
| namespaces | {"builtin": 7, "ur5e_dt": 23} |
| quick_start_s | 5.2 |
| quick_start_status | success |
| sim_window | Isaac Sim Python 6.2.0 1440x900 |
| shot_mean | 43.06 |
| shot_nonblack | 0.766 |
| add_objects_status | success |
| pose_publisher_status | success |
| play_scene_status | success |
| shot2_mean | 41.6 |
| shot2_nonblack | 0.76 |
| sim_stop | {"pid": 425318, "port_closed_in_s": 0.0, "already_dead": false} |

| Check | Result |
|---|---|
| Kit stage is up (app ready) | PASS — {"ready": {"ready_in_s": 3.2, "stage": true, "app_ready": true, "prims": 11}} |
| at least 30 bridge tools | PASS — {"total": 30} |
| at least 23 ur5e_dt tools | PASS — {"ur5e_dt": 23} |
| quick_start_with_workstation succeeded | PASS — {"message": "Quick start complete with the Vention workstation loaded."} |
| S2-01 shows a rendered viewport (not black) | PASS — {"nonblack_frac": 0.766, "mean": 43.06} |
| add_objects(fmb1) | PASS — {"status": "success"} |
| setup_pose_publisher | PASS — {"status": "success"} |
| S2-02 shows a rendered viewport (not black) | PASS — {"nonblack_frac": 0.76} |

Artifacts: [Kit stdout/stderr](logs/sim.log), [tools.json](data/tools.json), [S2-01-workstation-loaded.png](shots/S2-01-workstation-loaded.png), [S2-02-objects-playing.png](shots/S2-02-objects-playing.png)


## S3 — ROS TF: PASS

| Key | Value |
|---|---|
| camera_tool | ur5e_dt.setup_camera_tf_graph |
| camera_setup | {"camera_prim": true, "graph_prim": true} |
| seconds | 5.4 |
| exit_code | 0 |
| objects_poses_sim_frames | 5 |
| tf_cam_frames | 1 |
| objects_poses_sim_msgs | 437 |
| tf_cam_msgs | 437 |

| Check | Result |
|---|---|
| objects_poses_sim >= 5 frames | PASS — {"frames": 5} |
| tf_cam >= 1 frame | PASS — {"frames": 1} |

Artifacts: [tf_capture output](logs/tf_capture.txt), [tf.json](data/tf.json)


## S4 — Motion video: PASS

| Key | Value |
|---|---|
| start_status | success |
| sweep_seconds | 10.01 |
| sweep_frames | 427 |
| sweep_joints | 3 |
| sweep_delta | {"shoulder_pan_joint": -0.3786, "shoulder_lift_joint": -0.1008, "elbow_joint": 0.2763} |
| stop_status | success |
| recorded_frames | 434 |
| recorded_seconds | 14.47 |
| stop_wall_s | 10.2 |
| before | 168082 |
| after | 174123 |
| video_bytes | 3938100 |
| video_mb | 3.94 |
| duration_s | 14.47 |
| pose_diff_frac | 0.0146 |
| pose_diff_mean | 1.86 |
| before_mean | 41.46 |
| after_mean | 41.48 |

| Check | Result |
|---|---|
| start_recording accepted | PASS — {"status": "success"} |
| joints moved while recording | PASS — {"delta": {"shoulder_pan_joint": -0.3786, "shoulder_lift_joint": -0.1008, "elbow_joint": 0.2763}} |
| video > 50 KB | PASS — {"bytes": 3938100} |
| robot in a different pose after the sweep | PASS — {"frac_changed": 0.0146, "mean_abs": 1.86} |
| still frames are rendered viewports | PASS — {"nonblack": [0.76, 0.761]} |

Artifacts: [S4-02-before-sweep.png](shots/S4-02-before-sweep.png), [S4-03-after-sweep.png](shots/S4-03-after-sweep.png), [S4-01-joint-sweep.mp4](videos/S4-01-joint-sweep.mp4)


## S5 — Isaac Lab tools: PASS

| Key | Value |
|---|---|
| isaaclab_version | 3.0.0 |
| isaacsim_version | 6.2.0.0 |
| lab_python | /home/kacolem8@ad.asu.edu/Desktop/ur5e-digital-twin/learning6/.venv/bin/python |
| lab_root | /home/kacolem8@ad.asu.edu/Desktop/ur5e-digital-twin/learning6/IsaacLab |
| envs_seconds | 3.5 |
| ur5e_envs | 1 |
| reach_envs | 14 |
| import_errors | None |

| Check | Result |
|---|---|
| isaaclab 3.0.0 | PASS — {"found": "3.0.0"} |
| isaacsim 6.2.0.0 | PASS — {"found": "6.2.0.0"} |
| UR5e-Cell-Reach-Direct-v0 listed | PASS — {"found": true} |
| no import errors | PASS — {"errors": null} |

Artifacts: [lab_install.json](data/lab_install.json), [lab_envs.json](data/lab_envs.json)


## S6 — Guards: PASS

| Key | Value |
|---|---|
| kit_guard_refused | True |
| kit_guard_ports | [8791] |
| job1 | 20261030-100930-Isaac-Reach-UR10 |
| job_guard_refused | True |
| cancel | {"cancelled": true, "state": "cancelled"} |
| state_after_cancel | cancelled |
| job_after_cancel | 20261030-100932-Isaac-Reach-UR10 |
| other_jobs | 2 |

| Check | Result |
|---|---|
| Kit app blocks an Isaac Lab job (refused, nothing started) | PASS — {"error": "a Kit bridge (Isaac Sim app) is running at localhost:8791; it shares the GPU with this job"} |
| refusal names the twin port | PASS — {"port": 8791} |
| second job refused while one runs | PASS — {"error": "another Isaac Lab job is already running: 20261030-100930-Isaac-Reach-UR10 (pid 426517)"} |
| refusal names the running job | PASS — {"running_job_id": "20261030-100930-Isaac-Reach-UR10"} |
| cancel reports cancelled | PASS — {"response": {"cancelled": true, "state": "cancelled"}} |
| job ends as cancelled (not failed) | PASS — {"state": "cancelled"} |
| a new job is accepted after cancel | PASS — {"job_id": "20261030-100932-Isaac-Reach-UR10", "error": null} |

Artifacts: [S6-kit-guard.json](data/S6-kit-guard.json), [S6-guard.json](data/S6-guard.json)


## S7 — Training: PASS

| Key | Value |
|---|---|
| calibration_iterations | 51 |
| calibration_rate_eps | 0.872 |
| projection_for_3000_s | 3440 |
| target_s | 300 |
| calibration_seconds | 90.1 |
| calibrated | True |
| dropped_calibration_job | 20261030-100932-UR5e-Cell-Reach-Direct-v0 |
| job_id | 20261030-101103-UR5e-Cell-Reach-Direct-v0 |
| max_iterations | 262 |
| state | succeeded |
| wall_s | 310.3 |
| job_elapsed_s | 306.161 |
| epochs | 262 |
| planned_iterations | 262 |
| fps_last | 23527 |
| fps_avg | 23500.2 |
| peak_vram_mib | 4600 |
| exit_code | 0 |
| checkpoint | /home/kacolem8@ad.asu.edu/Desktop/ur5e-digital-twin/learning6/IsaacLab/logs/rl_games/ur5e_cell_reach/2026-10-30_10-11-06/nn/last_ur5e_cell_reach_ep_262_rew__293.34763_.pth |
| run_dir | /home/kacolem8@ad.asu.edu/Desktop/ur5e-digital-twin/learning6/IsaacLab/logs/rl_games/ur5e_cell_reach/2026-10-30_10-11-06 |
| reward_min | -124.88 |
| reward_last | 270.56 |
| success_last | 0.536 |
| dist_m_last | 0.046 |

| Check | Result |
|---|---|
| training job succeeded | PASS — {"state": "succeeded", "exit_code": 0, "last_lines": ["MAX EPOCHS NUM!", "Training time: 277.83 seconds", "[INFO]: SimulationContext cleared"]} |
| reward rises during the run | PASS — {"first": -124.88, "last": 270.56} |
| curve plot(s) written | PASS — {"files": ["S7-01-reward-curve.png", "S7-02-success-curve.png", "S7-03-distance-curve.png", "S7-04-fps.png"], "exit_code": 0, "error": null} |

Artifacts: [job stdout](logs/S7-train.log), [S7-metrics.json](data/S7-metrics.json), [S7-01-reward-curve.png](shots/S7-01-reward-curve.png), [S7-02-success-curve.png](shots/S7-02-success-curve.png), [S7-03-distance-curve.png](shots/S7-03-distance-curve.png), [S7-04-fps.png](shots/S7-04-fps.png)


## S8 — Windowed policy play + visual feedback: PASS

| Key | Value |
|---|---|
| job_id | 20261030-101614-UR5e-Cell-Reach-Direct-v0 |
| checkpoint | /home/kacolem8@ad.asu.edu/Desktop/ur5e-digital-twin/learning6/IsaacLab/logs/rl_games/ur5e_cell_reach/2026-10-30_10-11-06/nn/last_ur5e_cell_reach_ep_262_rew__293.34763_.pth |
| window_after_s | 15 |
| log_line_seen | True |
| window_id | 33554445 |
| window_title | Isaac Lab 3.0.0 |
| shot_mean | 90.58 |
| shot_nonblack | 0.946 |
| shot_size | 1440x900 |
| video_bytes | 303929 |
| video_frames | 108 |
| video_fps | 7.18 |
| video_seconds | 15.05 |
| video_duration | 15.05 |

| Check | Result |
|---|---|
| Isaac Lab window appeared | PASS — {"window": "Isaac Lab 3.0.0 1440x900"} |
| 'Completed setting up the environment' in the log | PASS |
| screenshot is a rendered viewport (not black) | PASS — {"nonblack_frac": 0.946} |
| play video written | PASS — {"bytes": 303929} |

Artifacts: [runs_status + window](data/window.json), [S8-01-play-window.png](shots/S8-01-play-window.png), [S8-02-play.mp4](videos/S8-02-play.mp4)


## S9 — Screen tools: PASS

| Key | Value |
|---|---|
| target | Isaac Lab 3.0.0 1440x900 |
| covering_window | T3 Code (Beta) 1717x1455 |
| stacked_above | True |
| stack_index_cover | 70 |
| stack_index_isaac | 1 |
| cover_nonblack | 0.507 |
| cover_mean | 50.33 |
| frac_changed_vs_cover | 0.7014 |
| frac_changed_vs_uncovered | 0.0016 |
| frac_changed_vs_S2 | 0.3977 |
| covered_mean | 90.69 |
| covered_nonblack | 0.947 |

| Check | Result |
|---|---|
| a window overlaps the Isaac window from the X stacking order above it | PASS — {"covering_window": "T3 Code (Beta)", "stacked_above": true, "basis": "xwininfo -root -tree (XQueryTree) order"} |
| the covering window is really painted (not an unpainted shell) | PASS — {"mean": 50.33, "std": 70.91} |
| capture is the Isaac window, not the covering window | PASS — {"frac_changed": 0.7014, "mean_abs": 61.89} |
| capture is still the Isaac scene from before covering | PASS — {"frac_changed": 0.0016} |
| capture is a rendered viewport | PASS — {"nonblack_frac": 0.947} |

Artifacts: [Isaac window captured while covered](shots/S9-01-covered-window.png), [the window over the same rectangle](shots/S9-02-covering-window.png), [Isaac window before covering](shots/S9-00-uncovered.png)


## Cleanup

```
{
  "cancelled": [
    "20261030-101614-UR5e-Cell-Reach-Direct-v0"
  ],
  "port_open_after": false
}
```
