"""Regenerate docs/ECOSYSTEM.md (by capability) and docs/ECOSYSTEM-BY-REPO.md (by repository).

Each listed repository is shallow-cloned (or updated) under --workdir, and its Python source is
parsed with ``ast``. Nothing from those repositories is imported or executed. Tool names and
descriptions are taken from:

* functions decorated with ``@<anything>.tool`` / ``@<anything>_tool`` (FastMCP style);
* ``ToolDefinition(name="...", description=...)`` calls (omni-kit-mcp bridge style);
* module-level ``*TOOL_REGISTRY = {...}`` dict literals (isaac-sim-mcp twin style);
* ``class ...(FunctionBaseConfig, name="...")`` NeMo Agent Toolkit functions (NVIDIA's servers).

Usage::

    python3 scripts/ecosystem_catalog.py               # clone/update, write both pages
    python3 scripts/ecosystem_catalog.py --offline     # reuse existing clones only

Star counts and licenses come from ``gh api`` when the GitHub CLI is available and are left
blank otherwise.
"""

import argparse
import ast
import datetime as dt
import json
from pathlib import Path
import re
import subprocess

import ecosystem_capabilities as cap

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", "tests", "test", ".venv", "venv", "node_modules", "build", "dist"}

# (section, repo, summary). Summaries are written from each README; they are claims by the
# authors, not results of running the code.
REPOS = [
    ("nvidia", "NVIDIA-Omniverse/kit-usd-agents",
     "NVIDIA's official knowledge MCP servers (Apache-2.0), under `source/mcp/`: `isaacsim_mcp` "
     "(the Isaac Sim MCP Server in the Isaac Sim 6.x docs), `kit_mcp`, `omni_ui_mcp` and "
     "`usd_code_mcp`. They search documentation, APIs and code examples for coding assistants; "
     "they do not connect to a running simulator. The Source column shows which server a tool "
     "belongs to."),
    ("lab", "Milokucia/dex-isaac-mcp",
     "Keeps Kit running in a Docker daemon (Unix socket) and drives one articulation per robot "
     "JSON: normalised joint targets, named poses, range tests, gain tuning, headless captioned "
     "GIFs, plus Isaac Lab training via `docker compose run` (status, TensorBoard, checkpoints). "
     "Isaac Lab 2.3.2. Also on PyPI."),
    ("lab", "Tim0320/IsaacLab-MCP",
     "Windows, Isaac Lab 3.0.0 + Isaac Sim 6.0.1 (README in Traditional Chinese). Static task "
     "discovery, training design packets, an allowlisted Dofbot grasp/lift training worker, "
     "hash-verified evaluation with evidence bundles, and a versioned Sim -> Lab -> verification "
     "hand-off protocol."),
    ("lab", "chloepilonv/mcp-server-isaaclab",
     "Isaac Lab training, environment management and policy evaluation. A local stdio server "
     "forwards over an SSH tunnel to a FastAPI agent on a remote Brev GPU instance."),
    ("lab", "sandraschi/isaac-mcp",
     "FastMCP server covering Sim and Lab: scene depot, spawning, physics stepping, state tensors, "
     "a job scheduler, URDF to USD conversion, active-inference controllers and dashboard cards."),
    ("lab", "waynehacking8/physgate",
     "Research prototype: an LLM picks high-level robot skills, a critic prunes plans, and plans "
     "are validated in parallel Isaac Lab simulation before execution (Unitree Go2 tasks)."),
    ("sim", "omni-mcp/isaac-sim-mcp",
     "The original and most-starred Isaac Sim MCP: a Kit extension on port 8766 plus a stdio "
     "server. Scene creation, robots, lighting, script execution, text-to-3D via Beaver3D."),
    ("sim", "whats2000/isaacsim-mcp-server",
     "Fork of omni-mcp grown to 43 tools in 9 categories, 107+ robots from the asset library, "
     "step-and-observe debugging, hot reload, multi-instance ports. Isaac Sim 5.1-6.0.1, PyPI."),
    ("sim", "Tim0320/IsaacSim-MCP",
     "Extends whats2000/isaacsim-mcp-server for Windows and Isaac Sim 6.0.1 (README in Traditional "
     "Chinese): USD, robots, sensors, physics, Action Graph, ROS 2, Replicator SDG, animated "
     "people, NVIDIA assets, remote access through Tailscale."),
    ("sim", "GenRobo/isaac-sim-mcp",
     "Fork of omni-mcp for Isaac Sim 5.x: articulation inspection and joint control, playback, "
     "log capture, script execution, plus two Isaac Lab runtime bridge tools."),
    ("sim", "Gonglitian/isaac-sim-mcp-suite",
     "Isaac Sim MCP extension (omni-mcp based) combined with a MoveIt2 ROS 2 stack and "
     "DROID-compatible data collection in Isaac Lab."),
    ("sim", "nullbyte91/nvidia-isaac-mcp",
     "Kit extension listening on TCP 9876 plus a stdio server for Isaac Sim 5.x: stage and prim "
     "inspection, prim CRUD, physics and simulation control."),
    ("sim", "mochan-b/isaacsim-mcp",
     "Minimal server that sends Python to Isaac Sim's VS Code executor (127.0.0.1:8226) and "
     "reads console errors from the log file."),
    ("sim", "mateuszbestai/nvidia-omniverse-isaacsim-mcp-server",
     "Omniverse and Isaac Sim control over a WebSocket Kit extension on port 8766: scenes, "
     "animation, cameras, digital twins, assets, code execution."),
    ("omniverse", "serionist/omniverse-mcp",
     "MCP server plus Kit extension for Isaac Sim and other Omniverse apps: USD inspection and "
     "editing, viewport capture with segmentation, robots, physics, recordings."),
    ("omniverse", "krngrover6/omni-mcp",
     "Router MCP in front of NVIDIA's Omniverse MCP servers for USD Composer: stage, hierarchy, "
     "selection, cameras, lights, materials; forwards knowledge lookups upstream."),
    ("omniverse", "inbarajaldrin/omni-kit-mcp",
     "Upstream of this repository's bridge and gateway: one NDJSON bridge per Kit process with "
     "namespaced tool registries, hot reload, a control panel and a stdlib client."),
    ("omniverse", "inbarajaldrin/isaac-sim-mcp",
     "Upstream of this repository's UR5e twin pack (fork of omni-mcp/isaac-sim-mcp). Tools are "
     "declared in a registry dict and served by omni-kit-mcp."),
    ("other", "ros-claw/simscene",
     "Agent-oriented USD scene generation: NVIDIA USD Search, on-demand asset acquisition, "
     "editable scenes and Isaac Sim execution with evidence-based validation."),
    ("other", "dcmcshan/isaacsim-mcp",
     "Humanoid design-iteration workflow (actuator and link decomposition, Monte Carlo analysis); "
     "its MCP server covers configs and analysis rather than live Sim control."),
    ("other", "openhoward/dgx-isaacsim-mcp-public",
     "Configuration mirror for a three-plane DGX Spark deployment: Isaac Sim (whats2000 server) "
     "behind a TLS / bearer-token MCP gateway, used from a deny-by-default NemoClaw sandbox. "
     "Not runnable on its own."),
    ("other", "NMadhub/trakr-warehouse-mcp-workshop",
     "Workshop prompt pack: build a quadruped warehouse-inspection demo in Isaac Sim 6.0 by "
     "prompting Claude Code through omni-mcp/isaac-sim-mcp."),
    ("other", "ros-claw/g1-isaac-sim-mcp",
     "Announced MCP server for a Unitree G1 humanoid in Isaac Sim (ROSClaw)."),
    ("other", "Ylr9933/isaacsim-mcp", "Repository named for an Isaac Sim MCP server."),
]

FOCUS = {"nvidia": "NVIDIA docs", "lab": "Isaac Lab", "sim": "Isaac Sim", "omniverse": "Omniverse / USD", "other": "Other"}

SECTIONS = {
    "nvidia": "NVIDIA's MCP servers",
    "lab": "Servers that run Isaac Lab training, evaluation or policies",
    "sim": "Isaac Sim control servers",
    "omniverse": "Omniverse Kit and USD servers",
    "other": "Frameworks, deployments, workshops and placeholders",
}


def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def clone(repo, workdir, offline):
    path = workdir / repo.replace("/", "__")
    if not offline:
        if (path / ".git").exists():
            run(["git", "fetch", "-q", "--depth", "1", "origin", "HEAD"], cwd=path)
            run(["git", "reset", "-q", "--hard", "FETCH_HEAD"], cwd=path)
        else:
            run(["git", "clone", "-q", "--depth", "1", f"https://github.com/{repo}", str(path)])
    head = run(["git", "rev-parse", "HEAD"], cwd=path) if (path / ".git").exists() else None
    if not head or head.returncode:
        return path, None, None
    date = run(["git", "log", "-1", "--format=%cs"], cwd=path).stdout.strip()
    return path, head.stdout.strip(), date


def github_meta(repo):
    result = run(["gh", "api", f"repos/{repo}", "--jq",
                  "{stars: .stargazers_count, license: (.license.spdx_id // \"\"), "
                  "fork: .fork, parent: (.parent.full_name // \"\")}"])
    if result.returncode:
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def constant_text(node, strings):
    """Return the string a node evaluates to, if it is a literal or a module-level string name."""
    if isinstance(node, ast.Name):
        return strings.get(node.id)
    try:
        value = ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return None
    return value if isinstance(value, str) else None


def first_paragraph(text, limit=220):
    para = " ".join((text or "").strip().split("\n\n")[0].split())
    return para if len(para) <= limit else para[:limit - 1].rstrip() + "…"


def decorator_name(dec):
    fn = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return fn.id if isinstance(fn, ast.Name) else ""


def tools_in_file(path, rel):
    try:
        tree = ast.parse(path.read_text(errors="replace"))
    except (SyntaxError, ValueError):
        return []
    strings = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(
                node.targets[0], ast.Name):
            text = constant_text(node.value, {})
            if text is not None:
                strings[node.targets[0].id] = text
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if not decorator_name(dec).endswith("tool"):
                    continue
                name, desc = node.name, None
                if isinstance(dec, ast.Call):
                    for kw in dec.keywords:
                        if kw.arg == "name":
                            name = constant_text(kw.value, strings) or name
                        elif kw.arg == "description":
                            desc = constant_text(kw.value, strings)
                    if dec.args:
                        arg = constant_text(dec.args[0], strings)
                        if arg and " " in arg.strip():
                            desc = desc or arg
                        elif arg:
                            name = arg
                found.append((name, desc or ast.get_docstring(node) or "", rel, node.lineno))
        elif isinstance(node, ast.Call) and decorator_name(node) == "ToolDefinition":
            kws = {kw.arg: kw.value for kw in node.keywords}
            name = constant_text(kws["name"], strings) if "name" in kws else None
            if name:
                desc = constant_text(kws["description"], strings) if "description" in kws else ""
                found.append((name, desc or "", rel, node.lineno))
    # NeMo Agent Toolkit functions (NVIDIA kit-usd-agents): `class X(FunctionBaseConfig,
    # name="tool")`, described by a module-level `*_DESCRIPTION` string.
    nat_desc = next((v for k, v in strings.items() if k.endswith("_DESCRIPTION")), "")
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and any(
                getattr(b, "id", getattr(b, "attr", "")) == "FunctionBaseConfig" for b in node.bases):
            name = next((constant_text(kw.value, strings) for kw in node.keywords
                         if kw.arg == "name"), None)
            if name and nat_desc:  # undescribed configs are agent internals, not served tools
                found.append((name, nat_desc, rel, node.lineno))
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id.endswith("TOOL_REGISTRY")
                and isinstance(node.value, ast.Dict)):
            for key, value in zip(node.value.keys, node.value.values):
                name = constant_text(key, strings) if key is not None else None
                desc = ""
                if isinstance(value, ast.Dict):
                    for k2, v2 in zip(value.keys, value.values):
                        if k2 is not None and constant_text(k2, strings) == "description":
                            desc = constant_text(v2, strings) or ""
                if name:
                    found.append((name, desc, rel, key.lineno))
    return found


def extract(path):
    tools, seen = [], set()
    for file in sorted(path.rglob("*.py")):
        rel = file.relative_to(path)
        if SKIP_PARTS & set(rel.parts):
            continue
        for name, desc, rel_path, line in tools_in_file(file, rel):
            if (name, str(rel_path)) in seen:
                continue
            seen.add((name, str(rel_path)))
            tools.append({"name": name, "desc": first_paragraph(desc), "file": rel_path.as_posix(),
                          "line": line})
    return tools


def cell(text):
    return (text or "").replace("|", "\\|").replace("<", "&lt;").replace("\n", " ").strip()


NOTES = """- **Tools are extracted statically.** The generator parses each repository's Python with `ast`
  and lists functions decorated as MCP tools, `ToolDefinition(...)` calls, `*TOOL_REGISTRY`
  dicts and NeMo Agent Toolkit function configs. Nothing was installed or run, so a listed tool
  is one the code defines, not one that was seen working. Tools built dynamically, in other
  languages, or in test folders are not listed.
- **Descriptions are the authors' own words** (first paragraph of the docstring or description,
  shortened), including those written in Chinese. Neither they nor the summaries are an
  endorsement.
- **Source links are pinned** to the commit that was read, with the line where the tool is
  declared.
"""

AREA_HEADER = """# Isaac Sim and Isaac Lab MCP ecosystem, by capability

Every tool defined by other Isaac Sim, Isaac Lab and Omniverse MCP servers, grouped by what it
does and compared with what isaac-mcp offers today, to decide what is worth building. The same
tools listed repository by repository are in [ECOSYSTEM-BY-REPO.md](ECOSYSTEM-BY-REPO.md).
Generated by [`scripts/ecosystem_catalog.py`](../scripts/ecosystem_catalog.py) on {date} from
{repos} repositories ({tools} tools, {unique} distinct names); rerun it to refresh.

How to read this page:

- **Areas** come from name rules in
  [`scripts/ecosystem_capabilities.py`](../scripts/ecosystem_capabilities.py); a misfiled tool
  is fixed there. Tools from isaac-mcp's own upstreams ({upstream}) are left out.
- **isaac-mcp today**: *Have* means a named tool exists; *Partial* means only for the UR5e twin
  or part of the area; *run_python only* means an agent can do it by writing Kit Python;
  *Missing* means neither.
- **isaac-mcp today** includes the `sim__*` and `assets__*` pack added by PR #11
  (`exts/sim-tools`, documented in TOOLS-SCENE.md).
- **Planned** names a milestone (M1–M7) in
  [plans/SIM-TOOLS-FOLLOWUP.md](plans/SIM-TOOLS-FOLLOWUP.md); `—` means nothing is planned yet.
- **Priority** is a judgement for a robot-agnostic sim and training server: *High* = build
  next, *Medium* = worth it after that, *Low* = only for specific projects, *Skip* = do not
  rebuild, *Done* = covered.
- In the tables, tools with the same name in several repositories share one row; the
  description and source link come from the first repository listed.
{notes}"""

REPO_HEADER = """# Isaac Sim and Isaac Lab MCP ecosystem, by repository

Every repository in the catalogue and the tools it defines, in source order. For the same tools
grouped by capability, with isaac-mcp's coverage and build priorities, see
[ECOSYSTEM.md](ECOSYSTEM.md). Generated by
[`scripts/ecosystem_catalog.py`](../scripts/ecosystem_catalog.py) on {date}.

{notes}
For how to find these projects again: GitHub search misses most of them unless you try several
spellings (`isaacsim mcp`, `isaac-sim mcp`, `omniverse mcp`) or the `isaac-sim` + `mcp` topics.
"""

OFFICIAL = """## NVIDIA resources and community skills

| Resource | What it is | Where |
|---|---|---|
| Isaac Sim MCP Server | Documentation and code search for coding assistants (extensions, examples, settings), one of NVIDIA's knowledge servers in `kit-usd-agents`. It does not control a running simulator; its tools are under [Documentation and API search](#documentation-and-api-search). | [Isaac Sim docs](https://docs.isaacsim.omniverse.nvidia.com/latest/development_tools/isaac_sim_mcp.html), [source](https://github.com/NVIDIA-Omniverse/kit-usd-agents/tree/main/source/mcp/isaacsim_mcp) |
| Isaac Lab Agent Skills | 17 user and 7 developer skills: training, debugging RL, building environments, sensors, backends, migration. | [isaac-sim/IsaacLab `skills/`](https://github.com/isaac-sim/IsaacLab/tree/main/skills) |
| Isaac Sim Agent Skills | 44 skills: physics, USD, robot import, cameras, ROS 2, navigation, synthetic data, headless runs, troubleshooting. | [isaac-sim/IsaacSim `skills/`](https://github.com/isaac-sim/IsaacSim/tree/release/6.3/skills) |
| MIUAV `isaaclab` skill | Community skill (Apache-2.0, in Chinese), also on [claudskills.com](https://claudskills.com/skills/isaaclab/). Its examples use APIs that do not exist in Isaac Lab 3 (`isaaclab.algos.PPO`, `scripts/run.py`, `register_class`, `-v0` task ids); isaac-mcp borrowed only its outline. | [MIUAV/vibe-coding-ros2](https://github.com/MIUAV/vibe-coding-ros2/tree/main/agents/skills/simulator/isaaclab) |
| isaaclabdocs MCP | Hosted Isaac Lab documentation lookup; not examined further. | [mcpbundles](https://www.mcpbundles.com/skills/isaaclabdocs-mcp-566373b243) |
| Discussions | Driving Isaac Sim through the VS Code executor. | [NVIDIA forum](https://forums.developer.nvidia.com/t/mcp-tool-isaac-sim-mcp-server-with-the-vscode-extension/362042), [IsaacSim #512](https://github.com/isaac-sim/IsaacSim/discussions/512) |
| Thesis | Simulation-native (Isaac Sim MCP) vs middleware (ROS-MCP) orchestration; related work. | [Singhsh01/mcp-embodied-ai-thesis-](https://github.com/Singhsh01/mcp-embodied-ai-thesis-) |

isaac-mcp's own skill routes to the NVIDIA skills; see [AGENT-SKILLS.md](AGENT-SKILLS.md).
"""

PRIORITY_ORDER = ["High", "Medium", "Low", "Done", "Skip"]


def anchor(title):
    slug = re.sub(r"[^a-z0-9 -]", "", title.lower()).strip()
    return "#" + re.sub(r" +", "-", slug).replace(" ", "-")


def repo_labels(rows):
    """Short label per repository: the owner, or owner/name when an owner has several."""
    owners = {}
    for row in rows:
        owners.setdefault(row["repo"].split("/")[0], []).append(row["repo"])
    return {row["repo"]: (row["repo"] if len(owners[row["repo"].split("/")[0]]) > 1
                          else row["repo"].split("/")[0]) for row in rows}


def group_by_area(rows):
    areas = {key: {} for key in cap.AREAS}
    areas["other"] = {}
    for row in rows:
        if row["repo"] in cap.UPSTREAM or not row["sha"]:
            continue
        for tool in row["tools"]:
            entry = areas[cap.classify(row["repo"], tool["name"])].setdefault(
                tool["name"], {"repos": [], "tool": tool, "row": row})
            if row["repo"] not in entry["repos"]:
                entry["repos"].append(row["repo"])
    return areas


def tool_link(entry):
    row, tool = entry["row"], entry["tool"]
    return (f"https://github.com/{row['repo']}/blob/{row['sha']}/{tool['file']}"
            f"#L{tool['line']}")


def render_areas(rows, date):
    labels = repo_labels(rows)
    areas = group_by_area(rows)
    total = sum(len(e["repos"]) for a in areas.values() for e in a.values())
    unique = len({name for a in areas.values() for name in a})
    upstream = ", ".join(f"`{r}`" for r in sorted(cap.UPSTREAM))
    out = [AREA_HEADER.format(date=date, repos=len(rows), tools=total, unique=unique,
                              upstream=upstream, notes=NOTES)]

    def repos_in(key):
        counts = {}
        for entry in areas[key].values():
            for repo in entry["repos"]:
                counts[repo] = counts.get(repo, 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    out.append("## What to build next\n")
    out.append("Areas marked *High* or *Medium*, with the repositories that implement the most "
               "tools in each (worth reading before writing your own).\n")
    for level in ("High", "Medium"):
        for key, area in cap.AREAS.items():
            title, _, have, status, plan, priority, why = area
            if priority != level:
                continue
            refs = ", ".join(f"[{labels[r]}](https://github.com/{r}) ({n})"
                             for r, n in repos_in(key)[:3])
            out.append(f"- **[{title}]({anchor(title)})** ({priority}; isaac-mcp: {status}). "
                       f"{why} Planned: {plan}. Best references: {refs or 'none'}.")
    out.append("")

    out.append("## Areas at a glance\n")
    out.append("| Area | Software | Tools | Distinct | Repos | isaac-mcp today | Planned | Priority |")
    out.append("|---|---|---|---|---|---|---|---|")
    ordered = sorted(cap.AREAS.items(), key=lambda kv: PRIORITY_ORDER.index(kv[1][5]))
    for key, (title, software, _, status, plan, priority, _) in ordered:
        tools_n = sum(len(e["repos"]) for e in areas[key].values())
        out.append(f"| [{title}]({anchor(title)}) | {software} | {tools_n} | {len(areas[key])} | "
                   f"{len(repos_in(key))} | {status} | {plan} | {priority} |")
    out.append("")
    out.append(OFFICIAL)

    for key, (title, software, have, status, plan, priority, why) in ordered:
        out.append(f"## {title}\n")
        out.append(f"**Software:** {software}. **Priority:** {priority}. "
                   f"**isaac-mcp today ({status}):** {have} **Planned:** {plan}.\n")
        out.append(why + "\n")
        entries = sorted(areas[key].items(), key=lambda kv: (-len(kv[1]["repos"]), kv[0]))
        if not entries:
            out.append("No external tools in this area.\n")
            continue
        out.append("| Tool | Found in | What it does (author's description) |")
        out.append("|---|---|---|")
        for name, entry in entries:
            found = ", ".join(labels[r] for r in entry["repos"])
            out.append(f"| [`{cell(name)}`]({tool_link(entry)}) | {cell(found)} | "
                       f"{cell(entry['tool']['desc']) or '—'} |")
        out.append("")
    if areas["other"]:
        out.append("## Unclassified\n")
        for name, entry in sorted(areas["other"].items()):
            out.append(f"- [`{cell(name)}`]({tool_link(entry)}) ({labels[entry['row']['repo']]})")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_repos(rows, date):
    out = [REPO_HEADER.format(date=date, notes=NOTES), "## All repositories at a glance\n",
           "| Repository | Focus | Stars | License | Last commit | Tools found |",
           "|---|---|---|---|---|---|"]
    for row in rows:
        meta = row["meta"]
        out.append(f"| [{row['repo']}](https://github.com/{row['repo']}) | "
                   f"{FOCUS[row['section']]} | {meta.get('stars', '')} | "
                   f"{meta.get('license') or '—'} | {row['date'] or 'empty repo'} | "
                   f"{len(row['tools'])} |")
    out.append("")
    out.append("Stars and licenses are from the GitHub API on the generation date; `NOASSERTION` "
               "means GitHub could not classify the license file and `—` means GitHub reports "
               "no license\n(check the repository before reusing code either way).\n")
    for key, title in SECTIONS.items():
        out.append(f"## {title}\n")
        for row in (r for r in rows if r["section"] == key):
            repo, sha = row["repo"], row["sha"]
            out.append(f"### [{repo}](https://github.com/{repo})\n")
            meta = row["meta"]
            if meta.get("fork") and meta.get("parent"):
                out.append(f"Fork of `{meta['parent']}`.")
            out.append(row["summary"] + "\n")
            if not sha:
                out.append("The repository had no commits when this page was generated.\n")
                continue
            out.append(f"Read at [`{sha[:7]}`](https://github.com/{repo}/tree/{sha}) "
                       f"({row['date']}).\n")
            if not row["tools"]:
                out.append("No tool definitions were found by the static scan (see the notes at "
                           "the top).\n")
                continue
            out.append("| Tool | Area | What it does (author's description) | Source |")
            out.append("|---|---|---|---|")
            for tool in sorted(row["tools"], key=lambda t: (t["file"], t["line"])):
                link = f"https://github.com/{repo}/blob/{sha}/{tool['file']}#L{tool['line']}"
                area = ("isaac-mcp upstream" if repo in cap.UPSTREAM else
                        cap.AREAS.get(cap.classify(repo, tool["name"]), ("Unclassified",))[0])
                out.append(f"| `{cell(tool['name'])}` | {area} | {cell(tool['desc']) or '—'} | "
                           f"[{cell(tool['file'])}:{tool['line']}]({link}) |")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workdir", type=Path,
                        default=Path.home() / ".cache" / "isaac-mcp" / "ecosystem")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "ECOSYSTEM.md")
    parser.add_argument("--offline", action="store_true", help="reuse existing clones")
    args = parser.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for section, repo, summary in REPOS:
        path, sha, date = clone(repo, args.workdir, args.offline)
        tools = extract(path) if sha else []
        rows.append({"section": section, "repo": repo, "summary": summary, "sha": sha,
                     "date": date, "tools": tools, "meta": github_meta(repo)})
    date = dt.date.today().isoformat()
    args.out.write_text(render_areas(rows, date))
    by_repo = args.out.with_name(args.out.stem + "-BY-REPO.md")
    by_repo.write_text(render_repos(rows, date))
    total = sum(len(r["tools"]) for r in rows)
    print(f"wrote {args.out} and {by_repo.name} ({len(rows)} repositories, {total} tools)")


if __name__ == "__main__":
    main()
