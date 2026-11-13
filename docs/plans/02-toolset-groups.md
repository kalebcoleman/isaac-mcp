# Plan 02: toolset groups, off by default

Roadmap [Phase 0](../ROADMAP.md#phase-0-a-clean-surface). No simulator and no GPU. Start after the computer-use pull request
has merged, and rebase on it: it renames the desktop tools and this plan only regroups them.

## Problem

There are 15 toolsets and 8 are on by default, against the owner's rule that everything is off until asked for. Three
work-cell tools (`workcell__real2sim`, `workcell__planning`, `workcell__eval`) only answer "not implemented". `status` lists
six "planned" toolsets, three of which have no plan. The README shows counts, not which tools a group holds.

## Target

The [groups table](../ARCHITECTURE.md#groups) and the [tool mapping](../ARCHITECTURE.md#every-current-tool-mapped) are the
specification. In short:

| Toolset today | Becomes |
|---|---|
| `scene`, `physics`, `sensors`, `kit_debug`, and `code` without `run_script` | `sim` |
| `screen` | `computer` (use the name the computer-use pull request chose if it already made this group) |
| `robosim` | `real2sim` |
| `run_script` from `code` | joins `lab` |
| `core`, `assets`, `lab`, `ros2`, `workcell`, `input`, `launch`, `extensions` | Keep their names |
| `planning`, `eval` | Not created here: plan 03 adds each with its first tool |

- Every toolset's `default` is false except `core`. `extensions` is off too.
- `PROFILES`: `default` is `{"core"}`; `all` is unchanged in meaning; `minimal` is deleted.
- `PLANNED` and the `planned` key of `status` are deleted. An empty group is not listed.
- The three stubs are deleted everywhere, with no replacement tool.
- No aliases: an old toolset name in a configuration stops the server at startup with the list of valid names, which is what
  any unknown name does today.

Before writing code, confirm the seven open decisions in the [roadmap](../ROADMAP.md#decisions-needed) with the owner; items
1, 2, 4, 6 and 7 change this plan. Also ask for the seven shorter tool names from the rename pull request and apply the ones
the owner accepts here, so names change once.

## Files

| File | Change |
|---|---|
| `mcp/twinsmith/toolsets.py` | `TOOLSETS`, `_ROOTS`, `PROFILES`, `_UNGROUPED`; delete `PLANNED`; `describe()` loses `planned` |
| `mcp/twinsmith/tool_groups.py` | Delete the three stub groups |
| `mcp/twinsmith/tool_hints.py` | Delete the three stub hints |
| `mcp/twinsmith/server.py` | Instructions text, `_help` (it tests for `"code"`), `_register_host_tools` (`robosim` registers under `real2sim`), anything that names an old toolset |
| `mcp/twinsmith/config.py` | Profile parsing without `minimal` |
| `exts/workcell/workcell/extension.py`, `commands.py`, `bridge_binding.py` | Delete `real2sim_status`, `planning_status`, `eval_status` |
| `mcp/twinsmith/catalogs/workcell.json` | Regenerate |
| `scripts/gen_tool_reference.py` | The README block becomes group to tools (the `toolsets-full` rows); add the `cad` row as fixed text |
| `scripts/smoke.py`, `scripts/scene_mcp_client.py` | Toolset names |
| `mcp/tests/test_toolsets.py`, `test_smoke_runner.py`, `test_workcell_*.py`, `test_status_and_help.py`, `test_robosim_bridge.py` | Below |
| `README.md`, `AGENTS.md`, `.agents/skills/twinsmith/SKILL.md` | Generated table; quick start says to enable `sim` |
| `docs/TOOLSETS.md`, `docs/MCP-SERVER.md`, `docs/TOOLS-SCENE.md`, `docs/VERIFICATION.md`, `docs/ROBOSIM.md`, `docs/TOOLS-WORKCELL.md`, `exts/workcell/workcell/README.md`, `exts/workcell/docs/README.md` | Names, examples, the work cell's tool table |
| `docs/MIGRATING.md` | One table: old toolset name to new; the three deleted tools; `minimal` to `default` |
| `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/OPEN-ITEMS.md` | Move the mapping from "target" to "is"; close the items this resolves |

Find the rest with `git grep -n -w -e scene -e physics -e sensors -e kit_debug -e robosim -e minimal -- mcp scripts docs
README.md AGENTS.md .agents` and read each hit; most hits of `scene` and `physics` are ordinary words.

## Tests

- `toolsets.DEFAULT == {"core"}`; a server built with no selection lists exactly the `core` tools.
- Each group, enabled alone on top of `core`, lists exactly the tools in the mapping table. One parametrized test, the table
  written out in the test.
- Every registered tool has exactly one owner (the existing check).
- `status` has no `planned` key; `toolsets(action="enable", names=["sim"])` turns `sim` on at run time; `input` and `launch`
  still cannot be enabled at run time.
- `config.select("scene")`, `("minimal")` and `("robosim")` raise the unknown-name error that lists the valid names.
- No registered tool name contains `real2sim`, `planning` or `eval` under `workcell__`; the work-cell extension wiring test
  expects four grouped tools.
- Listing budgets: replace the three budgets with one per group and one for `all`, each the measured size plus about 5
  percent, measured with `scripts/measure_tool_list.py`.

## Acceptance checks

1. `twinsmith toolsets` prints the new table; only `core` is `on`.
2. `sim` alone measures below the old `default` budget of 15,900 estimated tokens. If it does not, stop and report the
   number: the fallback is to keep `kit_debug` out of `sim`.
3. `git grep -n "not implemented" -- exts/workcell mcp/twinsmith` finds no tool.
4. `python3 scripts/check_docs.py` passes with no page naming a deleted tool or an old toolset in a call.
5. The CI commands in the [plans index](README.md) pass.

## Not in this pull request

Regrouping the host modules into subpackages, removing the callable single-purpose names behind grouped tools, and the live
smoke checks for groups that have none. Each is its own change.
