# Assets: stores, project file and doctor

This page explains where the UR5e twin finds its robot, sensor and workstation files, and
how to move your own assets between machines and projects.

- Your assets live **outside** the isaac-mcp checkout, in an *asset store*.
- A project can override any asset with an `isaac-mcp.json` file.
- Keep stores in your workspace folder, `<workspace>/assets` ([WORKSPACE.md](WORKSPACE.md)).
- Nothing is silent: `where` shows what is searched, `doctor` shows what is found and
  verified, and strict mode refuses the NVIDIA download fallback.

Licences and what the repository may ship are in [asset notices](provenance/ASSETS.md).
This page is only about placement and checking.

## Layout

An asset store is a plain directory. Put files in a *pack* folder named `ur5e-dt`:

```text
<store>/
  ur5e-dt/                 # pack folder (preferred)
    SHA256SUMS             # optional user pins, coreutils format
    robot/ur5e.usd
    sensors/rsd455.usd
    workstation/vention.usd
  robot/ur5e.usd           # flat layout (older caches); still searched second
```

Inside a store the resolver tries `<store>/ur5e-dt/<relpath>` first, then
`<store>/<relpath>`. `<relpath>` is the logical asset name, for example `robot/ur5e.usd`.

## Lookup order

For each asset the first local copy wins:

1. **Project stores**: `asset_path` of the nearest `isaac-mcp.json`.
2. **Repo drop-in**: `exts/ur5e-dt/assets/<relpath>` (git-ignored for licensed files).
   Used as-is; it is not hash-checked.
3. **User stores**, in this order:
   1. `ISAAC_MCP_ASSET_PATH` (alias `UR5E_DT_ASSET_CACHE`), a list separated by `:`.
   2. `<workspace>/assets`, only when `ISAAC_MCP_WORKSPACE` is set and that folder exists.
   3. The old default `~/.cache/ur5e-dt/assets` (`$XDG_CACHE_HOME/ur5e-dt/assets` when that is
      set). It stays a default you can override; nothing existing is moved.
4. **NVIDIA remote**: only for manifest `remote` entries (`robot/ur5e.usd`,
   `sensors/rsd455.usd`), and never in strict mode.

Run `python3 scripts/isaac_assets.py where` to see the stores actually in use.

The Vention bench has no public source, so it only resolves from steps 1 to 3.

## Environment variables

Set these in the environment of the **Kit process** (and the shell that runs the scripts).
Setting them only in the MCP client does not change a running Kit.

| Variable | Meaning | Default | Deprecated alias |
|---|---|---|---|
| `ISAAC_MCP_ASSET_PATH` | User stores, separated by `:` on Linux (`os.pathsep`) | `~/.cache/ur5e-dt/assets` | `UR5E_DT_ASSET_CACHE` |
| `ISAAC_MCP_WORKSPACE` | Workspace folder. `<workspace>/assets` becomes a user store when this is set and the folder exists. See [WORKSPACE.md](WORKSPACE.md) | unset | none |
| `ISAAC_MCP_ASSET_STRICT` | `1`, `true`, `yes` or `on` refuses the NVIDIA remote fallback. `0` turns strict off even if the project file asks for it | off | `UR5E_DT_OFFLINE` |
| `ISAAC_MCP_ASSET_REMOTE_ROOT` | Base URL or mirror directory for NVIDIA assets | pinned Isaac 5.1 S3 root from the manifest | `UR5E_DT_ASSET_ROOT` |
| `ISAAC_MCP_PROJECT` | A project directory, or the `isaac-mcp.json` file itself | walk up from the working directory | none |

The new name wins when both are set. An old name still works and logs one deprecation
warning. The first user store is where `scripts/fetch_ur5e_assets.py` writes.

`ISAAC_MCP_ASSET_CONFIG` is a different setting: it approves project folders for the
`assets__*` import tools. See [TOOLS-SCENE.md](TOOLS-SCENE.md). It does not choose stores.

## The project file: `isaac-mcp.json`

Put it in the project root. The resolver finds it from `ISAAC_MCP_PROJECT`, or by walking
up from the working directory of the process.

```json
{
  "asset_path": ["./assets", "<workspace>/assets"],
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
python3 scripts/isaac_assets.py where --json

# Check every known asset. Exit 1 if any is corrupt.
python3 scripts/isaac_assets.py doctor
python3 scripts/isaac_assets.py doctor --json

# Also fail if these are not found locally.
python3 scripts/isaac_assets.py doctor --require robot/ur5e.usd --require workstation/vention.usd

# Fail if any asset would come from the NVIDIA remote.
python3 scripts/isaac_assets.py doctor --local-only

# Add a file, directory, or .tar/.zip archive to a store.
python3 scripts/isaac_assets.py add /path/to/lab-robot.tar.gz
python3 scripts/isaac_assets.py add ./vention.usd --as workstation/vention.usd
python3 scripts/isaac_assets.py add ./my-assets --store <workspace>/assets --pack ur5e-dt
python3 scripts/isaac_assets.py add ./ur5e.usd --as robot/ur5e.usd --force

# Re-hash the files in a pack and rewrite its SHA256SUMS.
python3 scripts/isaac_assets.py pin --store <workspace>/assets --pack ur5e-dt
```

| Command | What it does |
|---|---|
| `where [--json]` | Prints stores, project file, strict flag and any deprecated variable names in use. |
| `doctor [--json] [--require RELPATH] [--local-only]` | Resolves each known asset and reports `layer`, `status`, `path`, `sha256`. Exit 1 on a corrupt asset, a `--require` asset that is not local, or any remote asset with `--local-only`. |
| `add SRC [--store DIR] [--pack NAME] [--as RELPATH] [--force] [--max-members N] [--max-file-bytes N] [--max-total-bytes N]` | Copies a file, directory or archive into `<store>/<pack>/`. Extracts archives safely and refuses archives that expand past the limits (defaults: 20000 members, 8 GiB per file, 16 GiB in total). Archive headers count against the size limits too, and each extended tar header (PAX or GNU long name) is limited to 1 MiB. Writes through a private temporary file, created relative to an open folder handle that refuses symlinks, and never overwrites without `--force`. Refuses a store inside the repo checkout. Merges hashes into `<store>/<pack>/SHA256SUMS`. |
| `pin [--store DIR] [--pack NAME]` | Records the current hashes of the pack's files as pins. |

`doctor` statuses: `ok` (matches the manifest), `pinned` (matches your pin), `unpinned`
(found, no hash to compare), `remote` (would come from NVIDIA), `corrupt`, `missing`.
`layer` is `project`, `dropin`, `store`, `remote` or `missing`.

> The CLI is being added in the same PR as this page. If `--help` shows different options,
> trust `--help`.

## Set up a new machine

```bash
# Layout and first steps: see WORKSPACE.md. <workspace> is your workspace folder.
git clone <repo-url> <workspace>/isaac-mcp && cd <workspace>/isaac-mcp
./install.sh --accept-eula

# One time: put your private archive(s) in a store outside the checkout.
export ISAAC_MCP_WORKSPACE=<workspace>          # add to your shell profile
python3 scripts/isaac_env.py init               # creates <workspace>/assets and the rest
python3 scripts/isaac_assets.py add /path/to/lab-assets.tar.gz --store "$ISAAC_MCP_WORKSPACE/assets"

# Public NVIDIA files (needs network and NVIDIA's terms), if you want them local:
python3 scripts/fetch_ur5e_assets.py

# Prove it.
python3 scripts/isaac_assets.py doctor --require robot/ur5e.usd --require workstation/vention.usd
```

Then launch with the same variables set: `ISAAC_MCP_ASSET_STRICT=1 ./run.sh --port 8766`.

## Move between projects

Give each project its own `isaac-mcp.json`. Everything else stays the same.

```text
<workspace>/projects/project-a/isaac-mcp.json   {"asset_path": ["./assets"], "strict": true}
<workspace>/projects/project-b/isaac-mcp.json   {"asset_path": ["<workspace>/assets-v2"]}
```

```bash
cd <workspace>/projects/project-a && python3 <repo>/scripts/isaac_assets.py where
```

Or select the project without changing directory:
`ISAAC_MCP_PROJECT=<workspace>/projects/project-b python3 scripts/isaac_assets.py doctor`.

The resolver finds the project file from the **Kit process's** working directory or its
`ISAAC_MCP_PROJECT`. Start Kit from the project directory, or export `ISAAC_MCP_PROJECT`
before launching it. To switch projects, restart Kit with the new setting.

## Verify on two machines

Paths differ per machine. Hashes must not.

```bash
python3 scripts/isaac_assets.py doctor --json > doctor-$(hostname).json
```

Compare the `assets` entries between the two files: each `relpath` must have the same
`status` and the same `sha256`. Ignore `path`. A difference in `sha256` means the two
machines would load different bytes.

## Troubleshooting

**`AssetMissing: Asset not available: robot/ur5e.usd`**
Nothing local and no allowed remote. Read the listed `stores:` lines. Run
`python3 scripts/fetch_ur5e_assets.py`, or `isaac_assets.py add` your copy. Check Kit has
the same `ISAAC_MCP_ASSET_PATH` as your shell.

**`AssetMissing ... offline`**
Strict mode is on and there is no local copy. Add the file, or unset
`ISAAC_MCP_ASSET_STRICT` to allow the NVIDIA download.

**`AssetCorrupt: sha256 mismatch`**
The file differs from its pin or the manifest. If you changed it on purpose, run
`isaac_assets.py pin`. Otherwise restore it (`fetch_ur5e_assets.py --force` for public
files). For group files (the sensor's MDL and textures), keep the whole folder together.

**The robot loaded, but I expected my copy (silent remote fallback)**
Without strict mode, a missing local file loads from NVIDIA. Check with:

```bash
python3 scripts/isaac_assets.py doctor --local-only
```

It exits 1 and shows `layer: remote` for each asset that would be downloaded. Fix by
adding the file, then set `ISAAC_MCP_ASSET_STRICT=1` so it cannot happen again.

**Project file seems ignored**
Run `isaac_assets.py where` from the same directory Kit started in. Check
`ISAAC_MCP_PROJECT`, which wins over the directory walk. A bad path in `ISAAC_MCP_PROJECT`
means no project file, not a fallback to the walk.

**`assets__prepare_workstation` wrote to a different place**
That tool writes `workstation/vention.usd` directly under the **first** entry of
`ISAAC_MCP_ASSET_PATH` (or, without it, `UR5E_DT_ASSET_CACHE`, then `<workspace>/assets` if
`ISAAC_MCP_WORKSPACE` is set and that folder exists, then the default cache), in the
flat layout, not inside the `ur5e-dt` pack folder and without a pin. The resolver finds the
flat layout, and the file shows as `unpinned` in `doctor`. Both the MCP server and the Kit it
starts must see the same `ISAAC_MCP_ASSET_PATH`. Check with
`doctor --require workstation/vention.usd`.
