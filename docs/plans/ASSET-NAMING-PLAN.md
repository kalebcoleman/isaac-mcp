# Plan: generic naming, packs and asset stores

Status: proposal. **Not implemented in this PR** except where marked *(done)*.

Legend: **[verified]** I checked it in the code of this checkout. **[unverified]** I did not.

## Problem

The tool is called isaac-mcp, but much of the configuration and the asset code carries the
lab's UR5e twin name (`UR5E_DT_*`, `~/.cache/ur5e-dt`). A user of another robot has to set
UR5e-named variables. Lab-specific pieces and generic pieces are mixed.

## Goals

1. Core settings use `ISAAC_MCP_*`.
2. Old names keep working as deprecated aliases for at least one release.
3. Lab-specific UR5e tools become an optional **pack**.
4. Each pack has its own folder inside an asset store.
5. One command, `assets doctor`, says what is found and verified.

## Current state

- **[verified]** `asset_cache.py` reads `ISAAC_MCP_ASSET_PATH`, `ISAAC_MCP_ASSET_STRICT`,
  `ISAAC_MCP_ASSET_REMOTE_ROOT`, `ISAAC_MCP_PROJECT`, with `UR5E_DT_ASSET_CACHE`,
  `UR5E_DT_OFFLINE`, `UR5E_DT_ASSET_ROOT` as aliases that log one warning *(done)*.
- **[verified]** Pack folder `ur5e-dt` is a constant (`PACK`) inside a store, then the flat layout *(done)*.
- **[verified]** `ISAAC_MCP_PORT` is read by the bridge (`knobs.py`), `run.sh` exports it, and
  `verify.sh` falls back to it. `run.sh` itself still reads `UR5E_DT_PORT` for its own port.
- **[unverified]** `ISAAC_MCP_PYTHON`: I found no reader in the code. `UR5E_DT_PYTHON` is read
  by `run.sh` and `verify.sh`. The alias needs to be added, not just documented.
- **[verified]** `exts/sim-tools/sim_tools/core.py` (`assets__prepare_workstation`) reads the first
  entry of `ISAAC_MCP_ASSET_PATH` (falling back to `UR5E_DT_ASSET_CACHE`) and writes
  `workstation/vention.usd` flat under it. It does not yet write into the pack folder or pin
  the file.
- **[verified]** `ISAAC_MCP_ASSET_CONFIG` (project approval for `assets__*` import tools,
  `mcp/isaac_mcp/project_assets.py`) is a separate mechanism from `ISAAC_MCP_ASSET_PATH`.

## Update: portable setup (follow-up PR)

- `run.sh` reads `ISAAC_MCP_PYTHON` (alias `UR5E_DT_PYTHON`), then `<repo>/.venv/bin/python`, then
  auto-detects with `scripts/isaac_env.py resolve-python`. This supersedes the **[unverified]**
  `ISAAC_MCP_PYTHON` item above.
- New `scripts/isaac_env.py` (`find`, `resolve-python`, `init`, `where`) and
  [WORKSPACE.md](../WORKSPACE.md): one workspace folder for the checkout, asset stores, environments,
  projects and runs.
- Store order gains `<workspace>/assets` when `ISAAC_MCP_WORKSPACE` is set and the folder exists,
  before the old `~/.cache/ur5e-dt/assets` default. Nothing existing is moved; the
  `~/.cache/isaac-mcp/assets` rename below is still open.

## Target naming

| Today | Target | Notes |
|---|---|---|
| `UR5E_DT_PORT` | `ISAAC_MCP_PORT` | `run.sh` should read the new name first |
| `UR5E_DT_PYTHON` | `ISAAC_MCP_PYTHON` | add reader to `run.sh`, `verify.sh`, docs |
| `UR5E_DT_ASSET_CACHE` | `ISAAC_MCP_ASSET_PATH` | *(done in resolver)*; also switch `sim-tools` |
| `UR5E_DT_OFFLINE` | `ISAAC_MCP_ASSET_STRICT` | *(done in resolver)* |
| `UR5E_DT_ASSET_ROOT` | `ISAAC_MCP_ASSET_REMOTE_ROOT` | *(done in resolver)* |
| `~/.cache/ur5e-dt/assets` | `~/.cache/isaac-mcp/assets` | keep the old path as a read-only fallback store |

Tool names stay. The `ur5e_dt__*` namespace does not change, so existing clients and
prompts keep working.

## Packs

A **pack** is an optional bundle: Kit extension(s), tools, an asset folder name and a
manifest. The core is the host server, bridge, generic `sim__*` / `assets__*` tools and
Isaac Lab tools. The UR5e twin becomes the first pack, `ur5e-dt`.

- Asset folder per pack: `<store>/<pack>/<relpath>` (already the layout) *(done)*.
- Each pack owns its manifest and its `SHA256SUMS` pins.
- `assets doctor` iterates over installed packs and reports each pack's assets.
- Tool namespace is the pack name: `ur5e_dt__*`.

## Staged migration (each step a small PR)

1. **Resolver, project file, CLI, docs** *(this PR)*.
2. `sim-tools` `prepare_workstation` writes into the pack folder and records a pin (it already honours `ISAAC_MCP_ASSET_PATH`).
3. `run.sh`, `verify.sh`, installer: read `ISAAC_MCP_PORT` / `ISAAC_MCP_PYTHON` first; keep `UR5E_DT_*` with a warning.
4. Move the generic parts of `asset_cache.py` into a core module; the pack passes its name and manifest.
5. Default store `~/.cache/isaac-mcp/assets`, old path as fallback.
6. Package the UR5e twin as a pack with its own manifest and an install switch.
7. Deprecation notice, then removal of old names in a later major release.

Every step keeps `scripts/check_docs.py` and the tool counts passing.

## Open questions

1. Is the pack repository public or lab-private? This decides where private manifests and pins live.
2. Should `ISAAC_MCP_ASSET_CONFIG` (approval gateway for importing arbitrary project folders)
   merge with `ISAAC_MCP_ASSET_PATH` (trusted stores for the twin)? They have different
   threat models today: one imports user USD, the other resolves fixed named assets.
3. Which `exts/ur5e-dt` modules are generic (asset resolver, recording, scene state) and which are
   UR5e-specific (workstation, robot loading)? **[unverified]** I did not audit the modules.
4. How long do deprecated aliases stay?
5. Should the project file also set non-asset settings (port, pack list)?
