# Assets: the local asset database

This page explains where the Isaac MCP finds every asset it uses (robot, sensor,
workstation, gripper, objects, layouts) and how to build up and move your own assets.

- **The repository ships no assets.** It is code only, so nothing with a licence question is
  ever pushed to GitHub.
- **Each machine has one local asset folder**, `isaac-mcp-assets`, next to the checkout, with a
  subfolder per project (a *pack*), for example `ur5e-dt/`. Every asset the MCP uses lives there,
  and it grows as you add or adopt assets. It stays on your machine.
- **A local copy always wins.** Nothing is fetched over it or silently replaced, so a lab's
  modified files are safe. Replacing a file takes an explicit `--force` or `force=true`.
- A project picks its pack and can override any asset with an `isaac-mcp.json` file.
- Nothing is silent: `where` shows what is searched, `doctor` shows what is found and
  verified, and strict mode refuses the NVIDIA download fallback.

Where each asset originally comes from, and under which licence, is in
[asset notices](provenance/ASSETS.md).

## Public files and lab files

`ur5e_dt__quick_start` works for anyone, using only the first row. The other rows belong to a specific lab's digital twin: the tools
stay available, but they need your own files.

| Files | Source | Without them |
|---|---|---|
| `robot/`, `sensors/` and their materials (15 files, about 26 MB) | NVIDIA, online. `python3 scripts/fetch_ur5e_assets.py` keeps a local copy | Loaded from NVIDIA's server, unless strict mode refuses |
| `gripper/husarion_rg2/`, `gripper/QuickChanger.usd`, `gripper/configuration/` | Your lab's files; see [notices](provenance/ASSETS.md#what-a-public-copy-can-ship). Converted from [husarion/ur_onrobot_rg2_ros](https://github.com/husarion/ur_onrobot_rg2_ros) by `tools/convert_husarion_rg2_urdf.py` and `tools/rewire_husarion_rg2_usd.py` | Quick Start skips the gripper and says so in its `gripper` result |
| `workstation/vention.usd` | Your own Vention export; not redistributable | `ur5e_dt__load_workstation` and `ur5e_dt__quick_start_with_workstation` build a placeholder bench. `ur5e_dt__quick_start` has no bench either way |
| `objects/`, `fmb_assembly*.json` | Your lab's files | `ur5e_dt__add_objects` and `ur5e_dt__assemble_objects` report what is missing |

`ur5e_dt__sync_real_poses` and a real-robot `ur5e_dt__start_ros_driver` also expect your lab's hardware. `./verify.sh` checks the complete
lab scene, so it needs all of these.

### When something comes from online

- A local copy **always** wins. The resolver looks in project stores, then your asset folder, and only then at NVIDIA, and only for `robot/ur5e.usd` and
  `sensors/rsd455.usd`.
- `ISAAC_MCP_ASSET_STRICT=1` (or `"strict": true` in `isaac-mcp.json`) forbids online loading. A missing file is then an error.
- Each Quick Start result lists every asset in `assets`, with `source` (`project`, `store`, `remote`, `missing`, or `snapshot` for a file loaded from a pack) and `location`, and the
  console warns when one loads from online. `python3 scripts/isaac_assets.py doctor` shows the same without starting Kit.
- `fetch_ur5e_assets.py` only ever writes to `ur5e-dt/`, never to a pack a project selected. It keeps a changed file that has no pin, lists it and exits 1.
  Accept your change with `isaac_assets.py pin`, or replace it with NVIDIA's copy with `--force`.
- `--force` does **not** replace a pinned file. To get NVIDIA's copy back, remove the pin, then fetch: `isaac_assets.py unpin robot/ur5e.usd`, then `fetch_ur5e_assets.py --force`.
- A changed file with no pin **refuses to load** with `AssetCorrupt`, rather than falling back to online. This is how a damaged file is told apart from a
  deliberate edit: run `pin` once to say it is deliberate.
- With several stores, new downloads go to the first. Put a store holding modified files first, so a fetched copy cannot shadow it.
- `ur5e_dt__load_simple_room` and LiDAR `config` presets use Isaac's own assets root, not this resolver; strict mode does not cover them.

### Use your own assets

Put them in your asset folder, in a subfolder for the project. Files in `ur5e-dt/` replace the twin's by name:

```bash
python3 scripts/isaac_assets.py add <file-or-archive>     # into ur5e-dt/, and pins it
python3 scripts/isaac_assets.py pin                       # accept files you edited in place
python3 scripts/isaac_assets.py adopt <project-folder>    # a whole project, in its own subfolder
python3 scripts/isaac_assets.py doctor                    # what is found, and where
```

A project with its own pack selects it with `"pack"` (see [Named packs](#named-packs)), and can copy just the files a USD needs with `pack-build`.

To use a folder elsewhere, set `ISAAC_MCP_ASSET_PATH` when starting Kit, or list it in a project's `isaac-mcp.json`. Kit reads these at startup,
so restart it after changing them. To import a project's own USD into any scene with `assets__import_path`, approve its folder as a
[trusted root](TOOLS-SCENE.md#one-time-approval).

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
    bricksim/                # a project's own pack
      SHA256SUMS
      isaac-mcp-pack.json    # its entry files (written by pack-build or create)
      robot/ur5e.usd …
    cool-robot-project/      # assets adopted from another project
```

Inside a store the resolver looks in `<store>/<pack>/<relpath>`. `<relpath>` is the logical
asset name, for example `robot/ur5e.usd`. Only the twin's `ur5e-dt` also tries `<store>/<relpath>`,
the flat layout older caches used.

## Named packs

The pack is chosen by, in order:

1. An explicit `pack=` argument (Python) or `--pack` (CLI).
2. `ISAAC_MCP_ASSET_PACK`.
3. `"pack"` in `isaac-mcp.json`.
4. `ur5e-dt`.

A name is 1 to 64 letters, digits, `.`, `_` or `-`, starting with a letter or digit, with no
path separators or `..`. An invalid name is an error wherever it comes from, not a silent skip.
Set the variable or project file for the Kit process **and** the MCP host: each reads its own
settings, and every pack tool reports what the host resolved.

What a pack means depends on who is asking:

| Who | Searches, in each store | NVIDIA fallback |
|---|---|---|
| The UR5e twin, in any project | the selected pack, then `ur5e-dt`, then the flat layout | Yes, for the manifest's `remote` entries, unless strict |
| `isaac_mcp.assets` with `ur5e-dt` selected | `ur5e-dt`, then the flat layout | The same as the twin |
| `isaac_mcp.assets` with another pack | only that pack | Never |

So a project that selects `bricksim` overrides twin files by name where its pack has them, and
the twin still finds everything else in `ur5e-dt`. Quick Start's `assets` list shows the path, and
adds `pack` when a file came from a pack other than `ur5e-dt`. A pack other than `ur5e-dt` never
borrows from another pack, so a missing file is an error you can see.

The hash rules still apply to the twin in a selected pack: a tracked file such as
`robot/ur5e.usd` must be pinned there, or it is refused as changed. Files built by `pack-build`
are pinned. `isaac_mcp.assets` with a named pack has no manifest, so an unpinned file there loads
and reports `status: unpinned`.

## Use the resolver from your project

```python
from isaac_mcp import assets      # standard library only, works without Kit

uri = assets.resolve_uri("robot/ur5e.usd")                 # the selected pack
path = assets.resolve_path("scene.usd", pack="bricksim")   # a named pack
info = assets.resolve_info("robot/ur5e.usd")               # never raises
```

Put `<checkout>/mcp` on `sys.path` or install the package. It is also what the twin uses. The
errors are `assets.AssetMissing`, `assets.AssetCorrupt` and `assets.InvalidPackName`. Python 3.10
or later. Installed without a checkout, the default store is `~/isaac-mcp-assets`, there is no
manifest and so no NVIDIA fallback.

`runpy.run_path("<checkout>/exts/ur5e-dt/ur5e_dt/asset_cache.py")` keeps working and returns the
twin's view, but it ties a project to the twin's folder. Prefer the import.

## Build a pack from a USD

`pack-build` copies a USD and only what it depends on, keeps the relative layout so references
still resolve, pins every file and records the entry files. It uses
`UsdUtils.ComputeAllDependencies`, then follows the textures named inside MaterialX and MDL files
with the same code the import validator uses, so a pack it calls self-contained passes that check.

```bash
# Files from another pack, by name:
python3 scripts/isaac_assets.py pack-build gripper/QuickChanger.usd workstation/vention.usd \
    --pack bricksim --from-pack ur5e-dt --dry-run

# A scene whose references are logical names (it is read for them, and not copied):
python3 scripts/isaac_assets.py pack-build <project>/assets/scene.usda --pack bricksim --from-pack ur5e-dt

# A project folder's own USD, keeping its layout:
python3 scripts/isaac_assets.py pack-build <project>/assets/robot/arm.usd --pack arm --layout-root <project>/assets
```

- The layout is relative to `--from-pack`, or `--layout-root`, or else the source's own folder.
- Anything unresolved, remote, unreadable or outside the layout **refuses the whole build**, lists
  each reference and the layer holding it, and copies nothing. `--allow-unresolved` builds anyway,
  and records what was missing so `doctor` accepts those and no others. It never copies a file from
  outside. A source USD that cannot be read always refuses.
- A bare MDL name such as `OmniPBR.mdl` comes from Kit's material library, so it is listed under
  `runtime_materials` and never blocks.
- A different file already in the pack is refused unless `--force`. Identical files are skipped, so
  building again changes nothing.
- Afterwards the closure is computed again in the new pack, and the result says whether it is
  `self_contained`. An absolute reference in a USD still points at the old file; this check finds it.
- A material file that cannot be read statically (a custom MDL module, a tiled or computed path,
  MaterialX `fileprefix`) is listed in `unfollowable` with the reason, and `self_contained` is false.
  `assets__import_path` refuses the same files.

**Where USD gets parsed.** The MCP host normally has no `pxr`, so the tools use the first of:

1. A helper process under `ISAAC_MCP_USD_PYTHON` (or `--usd-python`), any Python with `usd-core`:
   `uv venv <dir> && uv pip install --python <dir>/bin/python usd-core`. No Kit or GPU, and a
   parser crash cannot take down the server. The current interpreter counts if it has `pxr`.
2. The selected Kit, through `assets__dependencies` (MCP tools only).

Otherwise a build refuses with `USD_UNAVAILABLE`, and a doctor says the dependencies were not
checked. Native USD parsers are not sandboxed, so only use files you trust. Standalone USD and
Kit can resolve some references differently; the result names the `backend` that ran.

## Build a pack from a URDF

Approve the folder you cloned as a root (see [one-time approval](TOOLS-SCENE.md#one-time-approval)), then:

```text
assets__pack_build  pack="panda" sources=["/abs/example-robot-data/robots/panda_description/urdf/panda.urdf"]
sim__quick_start    robot="panda/example-robot-data/robots/panda_description/urdf/panda.urdf"
```

Loading a URDF needs the one-time staging setup even from a pack, because it's converted first; a USD pack doesn't ([one-time approval](TOOLS-SCENE.md#one-time-approval)). The build copies the URDF, each mesh it names and the textures a DAE, OBJ or MTL file names. It copies nothing else, so
a large repository is fine. No USD parser is needed. The source and the copy in the pack are never rewritten.

`package://<pkg>/<path>` is looked for inside the approved root or pack only, in this order:

1. a `package.xml` that names `<pkg>`, in any folder;
2. a folder called `<pkg>`;
3. the root itself, when its own folder is called `<pkg>`. The pack then keeps that folder name, so the package can still be found.

An unresolved package, a missing mesh, a symlink, a URL or a path that leaves the root refuses the build, naming each one
(`allow_unresolved=true` builds anyway, and records them). A texture a mesh names but that is absent does not
block: it is listed in `missing_textures`, and `self_contained` is false. `.glb`, `.gltf` and `.fbx` meshes are copied, but what
they name is not followed, which also makes `self_contained` false. A xacro file refuses with the command to expand it; this tool never runs xacro.

## Packs from MCP tools

| Tool | Writes | Does |
|---|---|---|
| `assets__packs` | No | Lists packs in every store, the selected pack and where that choice came from |
| `assets__pack_doctor` | No | The dependency-aware doctor below |
| `assets__dependencies` | No | The raw USD closure of files |
| `assets__pack_adopt` | Yes | Creates an empty pack, or copies a folder's asset files in and pins them. `files` limits it to named files or folders. `entries` records its root USDs; a sole top-level USD is recorded on its own |
| `assets__pack_pin` | Yes | Pins a pack's files |
| `assets__pack_build` | Yes | Builds a pack from USD or URDF files, as above |

These run on the MCP host and need no Kit, apart from USD parsing when no helper is set.

### Trust rules for the pack tools

Decided conservatively, because a model calls these tools. The CLI is run by you, so it has only
the rules marked *both*.

- **No tool can approve folders.** Trusted roots stay a [user action](TOOLS-SCENE.md#one-time-approval).
- **Writes go only to the primary asset folder** (the first user store). There is no destination
  argument, and a model cannot choose another path. Older read-only locations are never written.
- **Nothing is written inside the repository** (*both*), and a symlinked pack folder is refused.
- **`assets__pack_adopt` copies only from an approved root.** A symlink that leaves the root is
  refused. A broken approval file refuses with no fallback.
- **`assets__pack_build`, `assets__dependencies` and the USD check in `assets__pack_doctor` read
  from approved roots and user stores** (`ISAAC_MCP_ASSET_PATH` or the default folder). You set both
  outside the tools. A USD in an approved root cannot pull in other files: a dependency outside
  those folders is reported and never copied, even with `allow_unresolved`.
- **A project file's `asset_path` is not an approval.** It is found by walking up from the working
  directory, so a cloned repository could name any folder. The resolver still searches it, and
  `assets__pack_doctor` still checks its pins, but the tools do not parse USD there or copy from it
  until you approve that folder as a root.
- **A pack in a user store can be imported without approving it as a root:** a USD with `assets__import_path`, a URDF with `assets__import_urdf source_path`. You put it there, and
  the pack tools already read it. Both still need shared staging, refuse a pack
  whose files differ from their pins with `PACK_CORRUPT`, and refuse symlinks inside it.
- **No overwrite without `force=true`** (*both*). A different file in the pack is a refusal, with
  nothing copied.
- **Re-pinning is not implicit.** New files are pinned, but a pin that would change accepts whatever
  bytes are there, including damage. `assets__pack_pin` refuses with before and after hashes unless
  you name the file in `files` or pass `force=true`. The CLI's `pin` is your own decision and
  accepts everything.
- Trust covers the bytes you approved. It is not a sandbox.

## Check a pack: doctor

```bash
python3 scripts/isaac_assets.py doctor --pack bricksim --usd-python <python-with-usd-core>
python3 scripts/isaac_assets.py doctor --pack ur5e-dt --entry robot/ur5e.usd
```

It changes nothing. It checks every store holding the pack, because the first copy of a file wins.

| Reported | Meaning | Fails the check |
|---|---|---|
| `files.corrupt` | Differs from its pin (or the manifest, in `ur5e-dt`) | Yes |
| `files.missing` | Pinned, not on disk | Yes |
| `files.unpinned` | No hash to compare | No, but listed |
| `dependencies.unresolved` | A USD references a file that is not there, with the layer holding it | Yes |
| `dependencies.unreadable` | The file is there and USD cannot open it | Yes |
| `dependencies.remote`, `outside_pack` | The pack is not self-contained | Yes |
| `dependencies.missing_entries` | An entry file is gone | Yes |
| `dependencies.accepted_unresolved` | Recorded by `--allow-unresolved` when the pack was built | No |
| `dependencies.runtime_materials` | Supplied by Kit, such as `OmniPBR.mdl` | No |
| `dependencies.unfollowable`, `warnings` | A material file that cannot be read statically; `self_contained` is false and import refuses it | No: named as a warning, because the twin's own loading can still work |
| `unused` | No entry file reaches it through USD | No: information, because code can load files by name |

Entry files come from `--entry` (`entries`), then `isaac-mcp-pack.json`, then for `ur5e-dt` the names
the twin loads. Otherwise every USD counts as a root, and `unused` is not computed.

If USD could not run, `dependencies.checked` is false, `ok` is false, and the CLI exits 1. A clean
folder listing is not a pass. Use `--pins-only` to ask for the file checks alone.

`doctor` without `--pack` is the older report on logical names and works as before.

A real example on the UR5e pack: `robot/ur5e.usd` references `SubUSDs/Robotiq_2F_85_edit.usd`,
which is not there, so it is reported as unresolved. The four `robot/configuration/*` files are
reported as unused.

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
| `ISAAC_MCP_ASSET_PACK` | The [pack](#named-packs) to use. Beats `"pack"` in the project file | `ur5e-dt` | none |
| `ISAAC_MCP_USD_PYTHON` | A Python with `usd-core`, for [dependency checks](#build-a-pack-from-a-usd) on the host | the current interpreter if it has `pxr`, else the selected Kit | none |
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
  "pack": "bricksim",
  "asset_path": ["./assets", "<workspace>/isaac-mcp-assets"],
  "strict": true
}
```

| Key | Type | Meaning |
|---|---|---|
| `pack` | string | The [pack](#named-packs) to use. `ISAAC_MCP_ASSET_PACK` overrides it when set. |
| `asset_path` | list of strings | Stores searched **first**. Relative entries are relative to the file. `~` is expanded. |
| `strict` | boolean | `true` refuses the NVIDIA remote fallback. `ISAAC_MCP_ASSET_STRICT` overrides it when set. |

An invalid file (not JSON, not an object, `asset_path` not a list of strings, an invalid `pack`) is an error,
not a silent skip.

## User pins: `SHA256SUMS`

A robot modified by your lab is not NVIDIA's file, so the manifest hash would refuse it.
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
- `isaac_assets.py unpin <name>` removes a pin and leaves the file. A public file is then checked
  against NVIDIA's hash again, and `fetch_ur5e_assets.py --force` can replace it.

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

# Remove a pin so NVIDIA's copy can come back.
python3 scripts/isaac_assets.py unpin robot/ur5e.usd

# Named packs: list them, make one, build one from a USD, check one.
python3 scripts/isaac_assets.py packs
python3 scripts/isaac_assets.py create bricksim
python3 scripts/isaac_assets.py pack-build robot/ur5e.usd --pack bricksim --from-pack ur5e-dt
python3 scripts/isaac_assets.py doctor --pack bricksim
```

| Command | What it does |
|---|---|
| `where [--json]` | Prints stores, project file, selected pack and where it came from, strict flag and any deprecated variable names in use. |
| `doctor [--json] [--require RELPATH] [--local-only]` | Resolves each known asset and reports `layer`, `status`, `path`, `sha256`. Exit 1 on a corrupt asset, a `--require` asset that is not local, or any remote asset with `--local-only`. |
| `doctor --pack NAME [--entry RELPATH] [--usd-python PY] [--pins-only] [--json]` | Checks a pack's files and USD dependencies. See [Check a pack](#check-a-pack-doctor). Exit 1 on a problem, or if dependencies could not be checked. |
| `packs [--json]` | Lists the packs in every store. |
| `create NAME [--store DIR]` | Creates an empty pack. |
| `pack-build USD... --pack NAME [--from-pack NAME] [--layout-root DIR] [--store DIR] [--usd-python PY] [--allow-unresolved] [--dry-run] [--force] [--json]` | Copies USD files and only what they depend on. See [Build a pack](#build-a-pack-from-a-usd). |
| `unpin RELPATH... [--store DIR] [--pack NAME]` | Removes pins and leaves the files. |
| `add SRC [--store DIR] [--pack NAME] [--as RELPATH] [--force] [--max-members N] [--max-file-bytes N] [--max-total-bytes N]` | Copies a file, folder or archive into `<store>/<pack>/` (default: the selected pack) and pins it. Extracts archives safely and refuses archives that expand past the limits (defaults: 20000 members, 8 GiB per file, 16 GiB in total; each extended tar header at most 1 MiB, 16 MiB in total). Never overwrites without `--force`. Refuses a store inside the repo checkout. |
| `adopt FOLDER [--pack NAME] [--include EXT] [--file RELPATH] [--entry RELPATH] [--dry-run] [--force]` | `--file` copies only that file or folder. `--entry` records a root USD; a sole top-level USD is recorded on its own. Copies the asset files (USD, URDF/xacro/SDF/MJCF, meshes, MDL/MaterialX, textures) of a project folder into `<store>/<pack>/`, keeping their layout and pinning them. Skips `.git`, virtual environments, `node_modules` and hidden folders. The pack defaults to the folder's name. |
| `migrate [--from CHECKOUT] [--git-rev REV] [--no-legacy-stores] [--dry-run] [--force]` | Copies an old checkout's `exts/ur5e-dt/assets` (the files git tracked, read from history, plus the untracked copies still in the working tree, which win) and old asset folders into `<store>/ur5e-dt/`. A file at a path NVIDIA's files use is kept unpinned only when it is NVIDIA's exact file; a different one (a lab-modified robot) or one an old folder pinned is pinned as yours, and a file you already pinned in the database is never replaced. Files that fail their old check (a changed pinned file, or an unpinned file at an NVIDIA path that is not NVIDIA's file) are skipped and reported, and the command exits 1, as it does when the checkout's git history cannot be read (shallow clone, ownership error); other packs in an old folder are left alone (copy them with `adopt`). Identical files are skipped; a different file already in the database is a refusal unless `--force`. Safe to run again. |
| `pin [--store DIR] [--pack NAME]` | Records the current hashes of the pack's files as pins, accepting every change. The pack defaults to the selected one. |

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

**`AssetMissing: Asset not available: objects/...`**
The database does not have it. The message shows where it is expected and the command that
adds it. An old checkout still holding the files: `isaac_assets.py migrate`.

**`AssetMissing ... offline`**
Strict mode is on and there is no local copy. Run `fetch_ur5e_assets.py` for NVIDIA's public
files, `isaac_assets.py add` for your own, or set `ISAAC_MCP_ASSET_STRICT=0`.

**`AssetCorrupt: sha256 mismatch`**
The file differs from its pin or the manifest. If you changed it on purpose, run
`isaac_assets.py pin`. To restore NVIDIA's copy of a public file: if it has no pin,
`fetch_ur5e_assets.py --force`. If it is pinned, `--force` keeps it, so run
`isaac_assets.py unpin <name>` first.

**`Invalid pack name`**
Fix `ISAAC_MCP_ASSET_PACK` or `"pack"`; `isaac_assets.py where` shows which one is in use. A folder
with a space in its name needs `adopt <folder> --pack <name>`.

**`USD_UNAVAILABLE`, or doctor says dependencies were not checked**
Set `ISAAC_MCP_USD_PYTHON` to a Python with `usd-core`, or (MCP tools only) start Kit with `sim-tools`.

**`UNRESOLVED_DEPENDENCIES`**
The closure is incomplete and nothing was copied. The result lists each reference and the layer
holding it. Fix them, widen `layout_root`, or pass `allow_unresolved=true`.

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
