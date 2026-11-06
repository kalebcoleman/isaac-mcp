# Experiment and showcase evidence template

Copy this template into the relevant run notes or results page. Fill it from actual outputs; use `not measured`
when a value is unavailable. Keep private workspace paths and sensitive details out of public files.

## Question and scope

- **Question / hypothesis:**
- **Task and milestone:**
- **Status:** planned / running / completed / failed / blocked
- **Simulation or hardware:**
- **What this does not demonstrate:**

## Reproduce

- **Code revision and any uncommitted changes:**
- **Simulator, Isaac Lab, backend, device and versions:**
- **Task, scene, robot, assets and their provenance:**
- **Configuration, commands, seeds, checkpoint and dependency versions:**
- **Training budget:** environments, steps/iterations and elapsed time, if applicable
- **Evaluation setup:** number of environments and completed episodes per seed, held-out conditions,
  termination criteria and success thresholds
- **Concurrent workloads and timing:** CPU/GPU load, real-time factor and whether wall times are comparable

## Results

- **Measured:** numerator and denominator for success, failure categories and relevant physical metrics;
  include per-seed results and spread or uncertainty when appropriate.
- **Baselines:** scripted/expert and earlier policy, measured on the same conditions where possible.
- **Assumed or privileged:** simulator-only state, scripted actions, altered geometry, ideal sensors or any
  other information not available to the intended robot.
- **Failed / not measured:** describe failures and missing coverage rather than excluding them silently.
- **Evidence:** paths or approved public links and SHA-256 hashes for logs, metrics, configs, checkpoints,
  images and video. Preserve raw evidence separately when it is too large or unsuitable for Git.

## Video and pictures

- Capture a recognizable establishing view, a clear action close-up and the final state. Include a failure
  when it explains a limitation. Verify the recording opens and actually shows the run described above.
- Label **simulation only**, task, scripted vs learned controller, relevant run/seed and any playback speedup.
  Explain simulated state changes rather than implying physical pickup or delivery.
- Remove personal paths, terminal secrets and private project information from frames and overlays. Verify
  asset permissions, attribution and any restrictions before posting.
- **Caption / accessible description:**
- **Source run and recording command:**
- **Media location, checksum and license/permission:**

## Review and delivery

- [ ] A second person or reviewer can trace each claim to the result and recording.
- [ ] Commands, seeds, thresholds and failures are included; no untested capability is presented as complete.
- [ ] Media and links work, playback speed is stated, and the public README contains no private data.
- [ ] Tests and checks relevant to the change are recorded with the code revision.
- [ ] The PR describes the actual scope, evidence and limitations; it is not merged without authorization.
