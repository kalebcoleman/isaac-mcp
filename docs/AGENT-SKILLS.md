# Agent skill: `isaac-mcp`

The repository ships one Agent Skill (a `SKILL.md` folder in the open format read by Claude Code,
Codex, OpenCode and Antigravity) that teaches an agent how to drive
this server: discover and verify a bridge, run Isaac Lab jobs within the GPU guards, inspect and
edit a live Isaac Sim stage with the generic `sim__*`/`assets__*` tools or `run_python`, and look at
the simulator itself. It complements the tool
references (`TOOLS-*.md`), which say what each tool does; the skill says in what order to use them
and when to stop and ask.

```
.agents/skills/isaac-mcp/          canonical copy (Codex, OpenCode, Antigravity)
├── SKILL.md                       ground rules, first calls, Lab and Sim workflows, troubleshooting
└── references/
    ├── isaac-lab.md               train/eval/play, Isaac Lab 3 conventions, guards, reading metrics
    ├── isaac-sim.md               bridge selection, generic scene/assets, run_python, capture, UR5e pack
    └── official-skills.md         NVIDIA's Isaac Lab and Isaac Sim skills, mapped to isaac-mcp tools
.claude/skills/isaac-mcp           symlink to the canonical copy (Claude Code)
```

The skill only helps if the client also has the MCP server configured; see
[client configuration](MCP-SERVER.md#4-client-configuration).

## Where each client finds it

Inside this repository nothing needs installing: start the agent in the checkout.

| Client | Project skills | User-wide skills | Checked here |
|---|---|---|---|
| Claude Code | `.claude/skills/<name>/` | `~/.claude/skills/<name>/` | yes: lists `isaac-mcp` (2.1.316, through the symlink) |
| Codex | `.agents/skills/` in each directory from the working directory up to the repo root | `~/.agents/skills/` | yes: lists `isaac-mcp` |
| OpenCode | `.opencode/skills/`, `.claude/skills/`, `.agents/skills/` | `~/.config/opencode/skills/`, `~/.claude/skills/`, `~/.agents/skills/` | yes: lists `isaac-mcp` |
| Antigravity | `.agents/skills/` (legacy `.agent/skills/` still read) | `~/.gemini/config/skills/` | no: from [Antigravity's skills docs](https://antigravity.google/docs/skills) only |

Sources: [Codex skills](https://developers.openai.com/codex/skills),
[OpenCode skills](https://opencode.ai/docs/skills/), [Antigravity skills](https://antigravity.google/docs/skills).
The "Checked here" column means a fresh headless session started in this checkout named the skill
when asked; it does not test how well the skill steers a task.

## Using it from other projects

To have the skill everywhere, link the canonical directory into each client's user skills
directory. Linking (not copying) keeps one maintained copy that updates with `git pull`:

```bash
SKILL="$PWD/.agents/skills/isaac-mcp"            # run from the isaac-mcp checkout
mkdir -p ~/.agents/skills ~/.claude/skills ~/.gemini/config/skills
ln -sfn "$SKILL" ~/.agents/skills/isaac-mcp        # Codex and OpenCode
ln -sfn "$SKILL" ~/.claude/skills/isaac-mcp        # Claude Code (OpenCode also reads this)
ln -sfn "$SKILL" ~/.gemini/config/skills/isaac-mcp # Antigravity
```

Codex documents that it follows symlinked skill folders. The skill's references point at
`docs/` in this repository by name, so they stay meaningful when the skill is read through a link.

## NVIDIA's skills

NVIDIA maintains Agent Skills in [isaac-sim/IsaacLab](https://github.com/isaac-sim/IsaacLab/tree/main/skills)
(BSD-3-Clause) and [isaac-sim/IsaacSim](https://github.com/isaac-sim/IsaacSim/tree/release/6.3/skills)
(Apache-2.0). They cover environment design, RL debugging, assets, sensors, ROS 2 and migrations;
the isaac-mcp skill does not repeat them. `references/official-skills.md` maps each one to the
isaac-mcp tools that carry it out and says which wins when they disagree (NVIDIA for Isaac APIs,
isaac-mcp for tool use and safety gates).

NVIDIA's skills link to files elsewhere in their repositories, so do not copy them flat. Either run
the agent inside an Isaac Lab or Isaac Sim checkout (both expose `.agents/skills` and
`.claude/skills`), or symlink the individual skill directories you want from a checkout into your
user skills directory.

## What was adapted, and from where

- **NVIDIA skills**: the Isaac Lab conventions in `references/isaac-lab.md` (suffixless task ids,
  `rl_library`, judging runs by task metrics, one variable per experiment) follow
  `isaaclab-training-rl-agents` and `isaaclab-debugging-rl-training`, and were checked against an
  Isaac Lab 3.0.0 checkout. No text was copied.
- **Community `isaaclab` skill** ([MIUAV/vibe-coding-ros2](https://github.com/MIUAV/vibe-coding-ros2),
  Apache-2.0, listed on claudskills.com): only its outline (when to use, quick reference,
  troubleshooting) was borrowed. Its code uses APIs that Isaac Lab 3 does not have, so none of it
  was used.
- Other community MCP servers and their tools are catalogued in [ECOSYSTEM.md](ECOSYSTEM.md).

## Keeping it current

`scripts/check_docs.py` checks the skill's local links and that every `isaaclab__*` / `ur5e_dt__*`
name it mentions is a registered tool. When the `sim`/`assets` pack catalogs are present (after
PR #11), it also checks `sim__*` / `assets__*` names against those catalogs. Renaming a tool fails
CI until the skill is updated.
When adding a tool group or a safety rule, update `SKILL.md` alongside the `TOOLS-*.md` page.
