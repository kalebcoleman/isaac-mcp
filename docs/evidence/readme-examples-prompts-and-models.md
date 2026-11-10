# How the README examples were made: exact prompts and models

This page records, for every example in the README's "Things you can ask", **who or what produced it,
which model, and the exact prompt where an agent was given one**. Home paths are replaced with `<home>` and the sandbox folder name with `sandbox`; nothing else in a
prompt was edited. Where a README prompt is only an example of what you could say, that is stated here.

| README example | Produced by | Model | Prompt actually used |
|---|---|---|---|
| Drop in a public robot by name (Franka) | Scripted tool calls, no agent prompt | Claude Sonnet 5.5 (the session that wrote the scripts) | None. The README prompt is an example |
| Build something from scratch (block tower) | Scripted tool calls | Claude Sonnet 5.5 | None. The README prompt is an example |
| Put a wheeled robot in a room (Nova Carter) | Scripted tool calls | Claude Sonnet 5.5 | None. The README prompt is an example. No LiDAR was created in that picture |
| Load your own robot (SO-ARM100) | Scripted tool calls through a throwaway host | Claude Sonnet 5.5 | None. The README prompt is an example |
| Pull a real humanoid from GitHub (G1, Barkour) | A tester agent in a sandbox | **Gemini 3.8 Flash**, thinking level high (Antigravity) | The brief below, word for word |
| Train a humanoid to walk (G1), two runs | Direct tool calls | Claude Sonnet 5.5 | None. It followed the request "do the walk training as a test for the mcp"; the README prompt is an example. Calls are listed below |
| Isaac Lab research workflow (not shown as a picture; its findings are in the checklist) | A tester agent in a sandbox | Gemini 3.8 Flash, thinking level high | The second brief below, word for word |

"Scripted tool calls" means a person-style request was not typed to an agent: the calls were written out in Python and
run against a throwaway headless Isaac Sim (6.2.0, PhysX), so those pictures show what the tools can build, not what an
agent chose to do.

## Brief 1: robot from GitHub (Gemini 3.8 Flash, thinking level high)

> I'm starting a robotics project and want to try an open-source robot from GitHub in Isaac Sim. Please pick one well-known robot repo (for example a Unitree robot, a Franka or UR arm, a Kinova or xArm arm, or a robot from MuJoCo Menagerie), clone it, get the robot into a simulated scene, make it move, and show me a picture. Then make the scene more interesting: some objects around it, a camera view, a LiDAR or other sensor if it fits, and see how it behaves when it moves or when something hits it. Try a second robot from a different repo if the first goes smoothly. I care a lot about what is hard, slow, confusing or impossible, so please keep notes as you go.
>
> How to work: the simulator tools are called with `<home>/.local/share/uv/tools/isaac-mcp/bin/python <home>/sandbox/g1/call.py <tool name> '<json arguments>'`. `... call.py --list` lists every tool. An Isaac Sim is already running headless on the default port, so you don't need to start or stop one. Read the docs in <home>/sandbox/g1/tree/docs only if the tool descriptions aren't enough.
>
> Rules: download only into <home>/sandbox/g1/downloads, one new folder per repo, and only from public repos of well-known robotics organisations. Don't run any script or build from a downloaded repo, don't install anything, don't use sudo, and don't touch anything outside <home>/sandbox/g1. The graphics card has 16 GB and one Isaac Sim is already using about 7, so don't start another simulator or any training job.
>
> Keep running notes in <home>/sandbox/g1/notes.md (what you tried, what worked, what failed, with the exact error text for failures) and update it every few steps. When you finish, add a section called "Wishlist": the tools or options you wished existed for serious robotics and reinforcement-learning work, and for each, what you tried instead. Be honest about anything you couldn't do.

The agent chose the Unitree G1 (`unitreerobotics/unitree_ros`) and Google Barkour (`google-deepmind/mujoco_menagerie`)
on its own. The sandbox gave it a frozen copy of the code, its own Isaac Sim 6.2 on a
private port, its own empty asset folder, and an approved root for its `downloads` folder only.

## Brief 2: Isaac Lab research workflow (Gemini 3.8 Flash, thinking level high)

> I'm planning a serious reinforcement learning project with a robot and I want to see how far Isaac Lab goes through these tools. Please get a feel for it the way a researcher would: see what tasks are available, pick a robot task that interests you (a manipulator reaching task, a legged robot, or anything else you find), train it for a short time, watch how the learning is going, then evaluate what it learned and give me a score. Try at least two different learning libraries on the same task and tell me how they compare. Then push on the things a real project needs: running the same training again with a different seed, trying a different setting such as number of environments or learning rate, picking up a run later, saving and finding checkpoints, recording a video of the policy, and comparing runs. See whether you can use a robot or task of my own instead of a built-in one. I care a lot about what is hard, slow, confusing or impossible, so please keep notes as you go.
>
> How to work: the tools are called with `<home>/.local/share/uv/tools/isaac-mcp/bin/python <home>/sandbox/g2/call.py <tool name> '<json arguments>'`. `... call.py --list` lists every tool. Read the docs in <home>/sandbox/g2/tree/docs only if the tool descriptions aren't enough.
>
> Rules: the graphics card has 16 GB, so run only one training or evaluation job at a time, keep them short (a few minutes at most each, a small number of environments), and wait for a job to finish or cancel it before starting the next. Don't start an Isaac Sim by hand. Don't install anything, don't use sudo, and don't touch anything outside <home>/sandbox/g2.
>
> Keep running notes in <home>/sandbox/g2/notes.md (what you tried, what worked, what failed, with the exact error text for failures) and update it every few steps. When you finish, add a section called "Wishlist": the tools or options you wished existed for serious robotics and reinforcement-learning work, and for each, what you tried instead. Be honest about anything you couldn't do.

It ran on Isaac Lab 2.3.2 with Isaac Sim 5.1.0. Its notes file ends with the "Wishlist" section it was asked for, and the
checklist's Isaac Lab items come from that section and its final message.

## The humanoid walking run: the exact tool calls

Made by Claude Sonnet 5.5 calling the tools directly (no tester agent, no natural-language brief), on Isaac Lab 2.3.2 with
Isaac Sim 5.1.0, an RTX 5080 with 16 GB:

| Step | Tool and arguments |
|---|---|
| Train | `isaaclab__train` with `task="Isaac-Velocity-Flat-G1-v0"`, `rl_library="rsl_rl"`, `num_envs=1024`, `max_iterations=300`, `seed=1`, `headless=true`, `timeout_s=1800` |
| Evaluate | `isaaclab__eval` with the same task, `checkpoint=<run>/model_299.pt`, `num_envs=16`, `num_episodes=16`, `max_steps=3000`, `headless=true`, `timeout_s=600` |
| Watch | `isaaclab__play` with `task="Isaac-Velocity-Flat-G1-Play-v0"`, the same checkpoint, `num_envs=6`, `headless=false`, `timeout_s=1500`, then a 40-second window recording cut to the 7-second GIF |

Results: training 152 s; evaluation 16 episodes, all 1,000 steps, mean return 13.3 (range 7.4 to 21.6).

### The longer run (same method, 1,500 iterations)

| Step | Tool and arguments |
|---|---|
| Train | `isaaclab__train` with `task="Isaac-Velocity-Flat-G1-v0"`, `rl_library="rsl_rl"`, `num_envs=1024`, `max_iterations=1500`, `seed=2`, `headless=true`, `timeout_s=2400` |
| Evaluate | `isaaclab__eval` with the same task, `checkpoint=<run>/model_1499.pt`, `num_envs=16`, `num_episodes=16`, `max_steps=3000`, `headless=true`, `timeout_s=600` |
| Watch | `isaaclab__play` with `task="Isaac-Velocity-Flat-G1-Play-v0"`, the same checkpoint, `num_envs=6`, `headless=false`, `timeout_s=420`, and a 40-second recording of the window |

Results: training 727 s (about 0.48 s per iteration); final speed-tracking reward 0.913 (0.373 after 300 iterations); mean
episode reward 21.4; episodes ended by a fall (base contact) 1.4% (39.5% after 300 iterations); evaluation of 16 episodes,
all 1,000 steps, mean return 34.4, range 32.5 to 35.6 (13.3, range 7.4 to 21.6, after 300). Different seeds (1 and 2), so
this is one run each, not an average.

### Recordings

The two 40-second window recordings (1440x900, 20 fps, no audio) are in the project's draft evidence release, not in git:

| File | Size | SHA-256 |
|---|---:|---|
| `g1-walk-300it-window.mp4` | 892326 | `af2dd00e6801f3b7cb11dd77140f91c888eb751ca725b13e6dd28d81b4e6657e` |
| `g1-walk-1500it-window.mp4` | 1008784 | `fd6780a3e37924e63de3a7dc0322123a7d1304db1c69585f1906d0a60db49d91` |

The GIFs in the README are 6 to 7 seconds cut from those clips and cropped to the viewport.

## What this does not show

- A single run of each. No example was repeated, and the walking gait was judged from a still frame check of the recording,
  not measured.
- The "prompts" in the README for the four scripted pictures and the walking run were written for the README. A different
  agent given those words may do something different, or fail; that is what the wishlist testing is for.
