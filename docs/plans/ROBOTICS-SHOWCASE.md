# Robotics experiments and showcase plan

This is a work plan, not a statement that the milestones below have been achieved. It coordinates Jenga,
reinforcement learning (RL), BrickSim, and a supporting mobile-robot mission. Current tool usage is in
[Isaac Lab tools](../TOOLS-ISAACLAB.md), [BrickSim](../BRICKSIM.md), and [ROS 2 and RViz](../ROS2-RVIZ.md).
The [working checklist](CHECKLIST.md) remains the tracker for platform changes.

## Boundary: MCP, projects and run data

Keep the MCP focused on reusable simulator, robotics, asset, ROS and training capabilities. A demo should
exercise those capabilities without requiring its task-specific rewards, policies, mission logic, saved
runs or media to become core MCP behavior. Keep small, maintained examples only when they demonstrate a
general feature and can be tested without making the project depend on an experiment.

The current quick-start extension includes Jenga and warehouse scene builders, and the repository includes
Jenga, BrickSim and reach examples. Treat these as existing behavior to evaluate, not a model for adding
more experiment-specific features. Before moving or removing a builder or example, inventory its imports,
public tool contract, documentation, tests, asset paths and checkpoints; propose and test a compatible
migration. Do not silently break existing users or unilaterally drop a supported option.

Keep each substantial demonstration or research project in its own project directory or repository, with
its own README, task configuration and source. Where a project needs an MCP feature, prefer a generic
capability rather than a project-named tool. The mobile mission can remain separate. Decide project
ownership and licensing before publishing or copying third-party code.

Keep environments, project source, temporary run output and curated showcase evidence distinct. A project's
README should point to reproducible, named runs and a small curated set of media; temporary jobs and caches
should not accumulate as project source. Inventory run folders, active processes, checkpoints, uncommitted
work and links before reorganizing. Keep a manifest and update references before any move; archive or remove
only after the owner approves the specific cleanup. Do not change active jobs or assume old-looking data is
safe to delete.

## Order and ownership

1. **Jenga manipulation and RL:** first priority. Establish a trustworthy evaluation, then improve robustness
   and work towards a complete turn.
2. **BrickSim learning:** second priority. Test what the policy learned, remove privileged observations where
   possible, and study realistic motion limits.
3. **Mobile robot mission:** supporting integration demonstration. Finish evidence for the existing navigation
   and request workflow before adding scene complexity or a second robot.
4. **MCP integration:** fix verified tool or compatibility issues needed by these experiments; preserve results
   from both supported Sim/Lab combinations. Report unsupported behavior separately from regressions.

For each workstream designate one owner of its notes, code, experiments and deliverables. Other workers should
use separate branches or isolated worktrees, coordinate shared GPU/CPU resources, and stop only their own
processes. Before launching, inspect the actual processes, free memory, lock ownership and available disk;
locks and free-memory checks do not reserve memory. Capture load and real-time factor when concurrent jobs
could affect timing. Follow the operator's current workspace rules and ports. Avoid duplicate runs and save
checkpoints and logs as work progresses.

## Jenga: from a push to a turn

**Starting point.** A scripted arm and a learned push policy have moved a chosen block in simulation. The push
setup is constrained; a separate pull experiment caused the tower to fall. Those results do not establish a
complete game or general manipulation.

- [ ] **Evaluation first:** define success before training: the selected block must be pushed or removed by a
  stated distance, the tower must remain standing through a stated settling period, and other blocks must stay
  within stated displacement limits. Report each condition separately as well as the combined result. Save the
  thresholds and episode termination rules with the run.
- [ ] **Robust push:** compare the existing policy and a scripted baseline on held-out starts, block fit, size,
  friction and pose variations. Record the tested ranges; distinguish easy and deliberately difficult cases.
  Use multiple training and evaluation seeds, report completed episode counts and failures, and examine tower
  movement rather than relying only on reward or a training-window success rate.
- [ ] **Grasp and pull:** diagnose the current falling-tower failure before training against it. Add one stage
  at a time: approach, contact, grasp, extraction and retreat. Measure block motion, grip/contact behavior and
  displacement of other blocks. Record failures, including ones where the block moves but the tower falls.
- [ ] **One complete turn:** combine block choice, push, grasp, pull and a defined end state only when each
  stage has repeatable evidence. State which observations and controls are available to the policy and which
  are supplied by the simulator. Placement on top is a later milestone unless it is actually implemented.
- [ ] **Show it:** capture a clear wide view and a closer view of the gripper/tower, plus at least one failure
  alongside a successful attempt. Label scripted and learned behavior, scenario and simulation status.

## BrickSim: learning with honest observations and speeds

**Starting point.** A scripted assembly demo works through the MCP bridge. An imitation-learning experiment
has shown promising simulated results, but one policy uses a connection signal outside the stock observation
set, and the expert's measured motion exceeds the intended real arm's limits. Treat existing measurements as
experimental until independently checked and documented.

- [ ] Reproduce the scripted expert and learned-policy evaluations with equal completed episodes per
  environment, recorded device/physics backend, multiple seeds and saved configurations. Count timeouts,
  wrong connections and dropped bricks; do not stop after the first successes finish.
- [ ] Evaluate policies with and without the connection signal and document every observation each receives.
  Compare multiple seeds and held-out initial conditions; do not describe simulator state as a real sensor.
- [ ] Measure joint speeds and test a speed-limited expert and policy. Report the limits chosen and whether
  the simulated controller actually obeys them; do not claim real-robot readiness from this test.
- [ ] Test a documented set of brick dimensions and poses, noting cases where the expert or task setup also
  fails. Investigate PPO or a curriculum only after the observation and speed baselines are understood.
- [ ] Record separate labeled clips for the scripted expert, learned policy and representative failure. Save
  checkpoints and a reproducible evaluation command for each clip.

## Mobile robot: finish the mission evidence

- [ ] Reproduce the station-to-station navigation and evaluate request handling across multiple fresh sessions
  and station pairs. Record successes, retries, failures, arrival errors, recoveries and travel/dwell times.
- [ ] Exercise a blocked route and an unreachable goal. Say whether an obstacle was present before the run or
  appeared during it; distinguish a simulator/load or action-server failure from a proven navigation defect.
  Document how collisions were detected and any gaps in that detection.
- [ ] Show pickup and delivery markers and explain in the video and README that loading/unloading are simulated
  state changes unless physical manipulation has actually been implemented and measured.
- [ ] Only after the single-robot mission is reproducible, consider a different generic layout and then a second
  robot. Record an explicit reason if a stage is deferred. Do not infer real-facility throughput or safety.

## Evidence, recording and GitHub delivery

Use the [experiment and showcase template](EXPERIMENT-EVIDENCE.md) for every experiment and demo. Keep raw logs,
checkpoints and large videos in the workspace's run/evidence storage; record their locations, hashes and the
commit/config needed to reproduce them. Do not commit private paths, credentials, private project details,
restricted assets or large generated files to the public repository. Check image pixels and video overlays as
well as file text, and verify asset permissions and attribution.

Each GitHub-ready slice should include a concise README section or linked results page, exact setup and run
instructions, measured results and limits, and a viewable clip or still that matches the claimed run. Put only
small, permitted media in Git; use the project's approved evidence or release location for larger files and
link it once it actually exists. Mark missing recordings, failed tests and future milestones plainly. A polished
video is not evidence that a policy is robust.

Before proposing a PR, run the repository's relevant local checks and CI workflow, record the commit and results,
review the diff and evidence, and keep unrelated changes out. Do not merge without the owner's instruction.
