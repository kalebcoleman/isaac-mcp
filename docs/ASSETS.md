# Assets: the local asset database

This page explains where the Isaac MCP finds every asset it uses (robot, sensor,
workstation, gripper, objects, layouts) and how to build up and move your own assets.

- **The repository ships no assets.** It is code only, so nothing with a licence question is
  ever pushed to GitHub.
- **Each machine has one local asset folder**, `isaac-mcp-assets`, next to the checkout. It
  is your asset database: every asset the MCP uses lives there, every project can use it, and
  it grows as you add or adopt assets. It stays on your machine.
- A project can override any asset with an `isaac-mcp.json` file.
- Nothing is silent: `where` shows what is searched, `doctor` shows what is found and
  verified, and strict mode refuses the NVIDIA download fallback.

Where each asset originally comes from, and under which licence, is in
[asset notices](provenance/ASSETS.md).

## Layout

```text
<parent>/
  isaac-mcp/                 # the checkout: code only
  isaac-mcp-assets/          # your asset database (never pushed)
    ur5e-dt/                 # pack folder for the UR5e twin
      SHA256SUMS             # pins: the hash of every file you added
      robot/ur5e.usd         # your lab robot (or NVIDIA's public one)
      workstation/vention.usd
      gripper/…  objects/…  fmb_assembly1.json …
      Isaac/…  robot/configuration/…   # NVIDIA's public files (fetch script)
    cool-robot-project/      # assets adopted from another project
```

Inside a store the resolver tries `<store>/ur5e-dt/<relpath>` first, then
`<store>/<relpath>` (the flat layout older caches used). `<relpath>` is the logical asset
name, for example `robot/ur5e.usd`.

## Lookup order

For each asset the first local copy wins:

1. **Project stores**: `asset_path` of the nearest `isaac-mcp.json`.
2. **User stores**: `ISAAC_MCP_ASSET_PATH` (alias `UR5E_DT_ASSET_CACHE`), a list separated by
   `:`. When it is set it **replaces** the defaults. When it is not set:
   1. `isaac-mcp-assets` next to the checkout (or `<workspace>/isaac-mcp-assets` when
      `ISAAC_MCP_WORKSPACE` is set). This is where new files are written.
   2. Older locations, read only and only if they still exist: `<workspace>/assets` and
      `~/.cache/ur5e-dt/assets`. Move them with `isaac_assets.py migrate`.
3. **NVIDIA remote**: only for manifest `remote` entries (`robot/ur5e.usd`,
   `sensors/rsd455.usd`), and never in strict mode.

Files left in an old checkout's `exts/ur5e-dt/assets/` are **not** used any more; `migrate`
moves them. Run `python3 scripts/isaac_assets.py where` to see the stores actually in use.

## Environment variables

Set these in the environment of the **Kit process** (and the shell that runs the scripts);
for the MCP, put them in the `isaac` server's `env`.

| Variable | Meaning | Default | Deprecated alias |
|---|---|---|---|
| `ISAAC_MCP_ASSET_PATH` | Stores, separated by `:` (`os.pathsep`); the first is where files are written | `isaac-mcp-assets` next to the checkout | `UR5E_DT_ASSET_CACHE` |
| `ISAAC_MCP_WORKSPACE` | Workspace folder; its `isaac-mcp-assets` becomes the default store. See [WORKSPACE.md](WORKSPACE.md) | unset | none |
| `ISAAC_MCP_ASSET_STRICT` | `1`, `true`, `yes` or `on` refuses the NVIDIA remote fallback. `0` turns strict off even if the project file asks for it | off | `UR5E_DT_OFFLINE` |
| `ISAAC_MCP_ASSET_REMOTE_ROOT` | Base URL or mirror directory for NVIDIA assets | pinned Isaac 5.1 S3 root from the manifest | `UR5E_DT_ASSET_ROOT` |
| `ISAAC_MCP_PROJECT` | A project directory, or the `isaac-mcp.json` file itself | walk up from the working directory | none |

The new name wins when both are set. An old name still works and logs one deprecation
warning.

`ISAAC_MCP_ASSET_CONFIG` is a different setting: it approves project folders for the
`assets__*` import tools. See [TOOLS-SCENE.md](TOOLS-SCENE.md). It does not choose stores.

## The project file: `isaac-mcp.json`

Put it in the project root. The resolver finds it from `ISAAC_MCP_PROJECT`, or by walking
up from the working directory of the process.

```json
{
  "asset_path": ["./assets", "<workspace>/isaac-mcp-assets"],
  "strict": true
}
```

| Key | Type | Meaning |
|---|---|---|
| `asset_path` | list of strings | Stores searched **first**. Relative entries are relative to the file. `~` is expanded. |
| `strict` | boolean | `true` refuses the NVIDIA remote fallback. `ISAAC_MCP_ASSET_STRICT` overrides it when set. |

An invalid file (not JSON, not an object, `asset_path` not a list of strings) is an error,
not a silent skip.

## User pins: `SHA256SUMS`

A robot modified by your lab is not NVIDIA's file, so the manifest hash would reject it.
To accept it, list it in a `SHA256SUMS` file **in the folder that holds the asset**
(normally `<store>/ur5e-dt/SHA256SUMS`):

```text
<64-hex-sha256>  robot/ur5e.usd
<64-hex-sha256>  sensors/rsd455.usd
```

This is the same format as `sha256sum` writes. Rules:

- A pinned file is checked against its pin only. The NVIDIA manifest hash is ignored.
- A file with no pin that the manifest tracks is checked against the manifest, with the
  rest of its fetch group. Each other member of that group is checked against its own pin if
  it has one, so you can modify and pin one sublayer without pinning the root file.
- A file the manifest tracks (the NVIDIA robot and sensor) must sit at its canonical layout
  in the store, because USD resolves its sublayers by relative path. A copy at the plain
  logical name (for example `sensors/rsd455.usd` when the manifest places it deeper) is
  accepted only if you pinned it.
- When stores are nested, the pins of the folder that actually holds the file decide.
- A name must not start or end with whitespace or contain control characters, because the
  pin file cannot represent it. `add` and `pin` refuse or skip such names.
- If a pinned file changes, you get `AssetCorrupt`. Restore it, or run `pin` if the
  change is intended.

`isaac_assets.py add` writes pins for you.

## Strict mode

Without strict mode, a missing `robot/ur5e.usd` silently becomes an NVIDIA `https://`
URL. That needs the network and is not hash-verified by this resolver.

With `ISAAC_MCP_ASSET_STRICT=1` (or `"strict": true`), a missing asset raises
`AssetMissing` with a list of everything searched. Use it for repeatable runs.
It does not firewall Kit or other USD references.

## The CLI: `scripts/isaac_assets.py`

Run from the checkout root. Every command works without Isaac Sim.

```bash
# What is searched, in order, and which project file is active.
python3 scripts/isaac_assets.py where

# Check every known asset. Exit 1 if any is corrupt.
python3 scripts/isaac_assets.py doctor
# Also fail if these are not local, or if anything would come from NVIDIA's server.
python3 scripts/isaac_assets.py doctor --require robot/ur5e.usd --require workstation/vention.usd --local-only

# Add a file, folder, or .tar/.zip archive (lands in isaac-mcp-assets/ur5e-dt/…).
python3 scripts/isaac_assets.py add /path/to/lab-workcell.tar.gz
python3 scripts/isaac_assets.py add ./vention.usd --as workstation/vention.usd

# Copy the asset files of a project you cloned into the database (its own pack folder).
python3 scripts/isaac_assets.py adopt ~/projects/cool-robot-project --dry-run
python3 scripts/isaac_assets.py adopt ~/projects/cool-robot-project

# Move assets from an older checkout (exts/ur5e-dt/assets, even from git history) and
# from old asset folders (~/.cache/ur5e-dt/assets) into the database.
python3 scripts/isaac_assets.py migrate

# NVIDIA's public robot and sensor files (never replaces a file you pinned).
python3 scripts/fetch_ur5e_assets.py

# Re-hash the files in a pack and rewrite its SHA256SUMS.
python3 scripts/isaac_assets.py pin
```

| Command | What it does |
|---|---|
| `where [--json]` | Prints stores, project file, strict flag and any deprecated variable names in use. |
| `doctor [--json] [--require RELPATH] [--local-only]` | Resolves each known asset and reports `layer`, `status`, `path`, `sha256`. Exit 1 on a corrupt asset, a `--require` asset that is not local, or any remote asset with `--local-only`. |
| `add SRC [--store DIR] [--pack NAME] [--as RELPATH] [--force] [--max-members N] [--max-file-bytes N] [--max-total-bytes N]` | Copies a file, folder or archive into `<store>/<pack>/` and pins it. Extracts archives safely and refuses archives that expand past the limits (defaults: 20000 members, 8 GiB per file, 16 GiB in total; each extended tar header at most 1 MiB, 16 MiB in total). Never overwrites without `--force`. Refuses a store inside the repo checkout. |
| `adopt FOLDER [--pack NAME] [--include EXT] [--dry-run] [--force]` | Copies the asset files (USD, URDF/xacro/SDF/MJCF, meshes, MDL/MaterialX, textures) of a project folder into `<store>/<pack>/`, keeping their layout and pinning them. Skips `.git`, virtual environments, `node_modules` and hidden folders. The pack defaults to the folder's name. |
| `migrate [--from CHECKOUT] [--git-rev REV] [--no-legacy-stores] [--dry-run] [--force]` | Copies an old checkout's `exts/ur5e-dt/assets` (the files git tracked, read from history, plus the untracked copies still in the working tree, which win) and old asset folders into `<store>/ur5e-dt/`. A file at a path NVIDIA's files use is kept unpinned only when it is NVIDIA's exact file; a different one (a lab-modified robot) or one an old folder pinned is pinned as yours, and a file you already pinned in the database is never replaced. Files that no longer match their old pin are skipped and reported; other packs in an old folder are left alone (copy them with `adopt`). Identical files are skipped; a different file already in the database is a refusal unless `--force`. Safe to run again. |
| `pin [--store DIR] [--pack NAME]` | Records the current hashes of the pack's files as pins. |

`doctor` statuses: `ok` (matches the manifest), `pinned` (matches your pin), `unpinned`
(found, no hash to compare), `remote` (would come from NVIDIA), `corrupt`, `missing`.
`layer` is `project`, `store`, `remote` or `missing`.

Folders the CLI creates are not group- or world-writable (the workstation tool refuses such
folders), and it removes group/world write permission from your own store and pack folders
when it writes to them.

## Set up a new machine

```bash
git clone <repo-url> isaac-mcp && cd isaac-mcp
./install.sh --accept-eula

# Fill the asset database (isaac-mcp-assets next to the checkout).
python3 scripts/isaac_assets.py add /path/to/your-assets.tar.gz   # your lab files
python3 scripts/fetch_ur5e_assets.py                              # NVIDIA's public files

# Prove it, then use strict mode so nothing silently comes from the internet.
python3 scripts/isaac_assets.py doctor --require robot/ur5e.usd --local-only
export ISAAC_MCP_ASSET_STRICT=1
```

A checkout that still has `exts/ur5e-dt/assets` (from before the repository became code-only)
only needs `python3 scripts/isaac_assets.py migrate` once.

## Use assets from another project

Clone the project, then either copy its assets into the database:

```bash
python3 scripts/isaac_assets.py adopt <path-to-clone>          # pack = the folder's name
```

or use them where they are, for that project only, with a project file in its root:

```json
{"asset_path": ["./assets"], "strict": true}
```

Start Kit from the project directory, or export `ISAAC_MCP_PROJECT=<project>` before
launching it. To switch projects, restart Kit with the new setting.

## Verify on two machines

Paths differ per machine. Hashes must not.

```bash
python3 scripts/isaac_assets.py doctor --json > doctor-$(hostname).json
```

Compare the `assets` entries between the two files: each `relpath` must have the same
`status` and the same `sha256`. Ignore `path`. A difference in `sha256` means the two
machines would load different bytes.

## Troubleshooting

**`AssetMissing: Asset not available: gripper/QuickChanger.usd`**
The database does not have it. The message shows where it is expected and the command that
adds it. An old checkout still holding the files: `isaac_assets.py migrate`.

**`AssetMissing ... offline`**
Strict mode is on and there is no local copy. Run `fetch_ur5e_assets.py` for NVIDIA's public
files, `isaac_assets.py add` for your own, or set `ISAAC_MCP_ASSET_STRICT=0`.

**`AssetCorrupt: sha256 mismatch`**
The file differs from its pin or the manifest. If you changed it on purpose, run
`isaac_assets.py pin`. Otherwise restore it (`fetch_ur5e_assets.py --force` for public
files).

**The robot loaded, but I expected my copy (silent remote fallback)**
Without strict mode, a missing local file loads from NVIDIA. `doctor --local-only` exits 1
and shows `layer: remote` for each such asset. Add the file, then set
`ISAAC_MCP_ASSET_STRICT=1`.

**`assets__prepare_workstation` refuses the folder**
It refuses a group- or world-writable store. Run any `isaac_assets.py` write command
(`pin` is enough) to tighten your own folders, or `chmod go-w` them.

**Project file seems ignored**
Run `isaac_assets.py where` from the same directory Kit started in. Check
`ISAAC_MCP_PROJECT`, which wins over the directory walk.
