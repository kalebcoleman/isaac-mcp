# Vendoring and Upstream Provenance

This repository consolidates two previously submodule-managed codebases into a single unified repository. This document records the provenance, upstream source repositories, fork locations, pinned commit SHAs, license status, and patch histories for all vendored components.

---

## 1. Provenance Record

| Component | Upstream Repository | Fork Repository | Pinned SHA | License Status | Local Delta / Patches |
|---|---|---|---|---|---|
| **`exts/ur5e-dt`** | `https://github.com/inbarajaldrin/isaac-sim-mcp` | `https://github.com/kalebcoleman/isaac-sim-mcp` | `27f6100` | MIT (`docs/provenance/isaac-sim-mcp-LICENSE`) | 1 file modified (`extension.py`); preserved in `docs/provenance/isaac-sim-mcp-ur5e-dt-owner-registrar.patch` |
| **`mcp/`** & **`exts/isaac.mcp.bridge`** | `https://github.com/inbarajaldrin/omni-kit-mcp` | `https://github.com/kalebcoleman/ur5e-mcp` | `9d0366b` | **No LICENSE file upstream** | Clean at `9d0366b` (no local patch) |

> [!NOTE]
> The `ur5e-dt` digital twin extension is this project's own work developed in a fork of Aldrin's upstream repository. The UR5e workstation digital twin, its scene definitions, controllers, checkpoints, and checks are **not** upstream's work.

---

## 2. Upstream Details and Pinned Commits

### `isaac-sim-mcp` (Extension Source)
- **Upstream:** `https://github.com/inbarajaldrin/isaac-sim-mcp`
- **Fork:** `https://github.com/kalebcoleman/isaac-sim-mcp`
- **Pinned Commit:** `27f6100` (*"ur5e-dt: add valid convexHull collider to the Quick-Changer"*)
- **Vendored Path:** `exts/ur5e-dt/` (the extension and its 111 MB of assets).
- **Dead code excluded:** `external/isaac-sim-mcp/isaac_mcp/` was not vendored because it was an obsolete host server implementation superseded by `omni-kit-mcp`.
- **Tools preserved:** Asset conversion utilities for the Husarion RG2 gripper were preserved under `tools/` (`convert_husarion_rg2_urdf.py`, `rewire_husarion_rg2_usd.py`).

### `omni-kit-mcp` (Bridge and Host Server)
- **Upstream:** `https://github.com/inbarajaldrin/omni-kit-mcp`
- **Fork:** `https://github.com/kalebcoleman/ur5e-mcp`
- **Pinned Commit:** `9d0366b` (*"feat(screen): default captures to the Isaac window for this port"*)
- **Vendored Paths:**
  - `mcp/` (host-side Python package `isaac_mcp`, exposing CLI `isaac-mcp`)
  - `exts/isaac.mcp.bridge/` (Kit bridge extension)
  - `exts/isaac.mcp.panel/` (Kit bridge GUI inspector panel)
- **Local modifications:** None at the time of consolidation. Fork commit `9d0366b` vendored clean.

---

## 3. License Status

- **`isaac-sim-mcp`:** MIT License. A copy of the upstream license is archived at `docs/provenance/isaac-sim-mcp-LICENSE`.
- **`omni-kit-mcp`:** **Upstream has no LICENSE file.** The repository was published publicly on GitHub without an explicit license file. Users and redistributors should note that upstream `inbarajaldrin/omni-kit-mcp` omits an explicit license grant.
- **Assets:** the repository ships none (it is code-only; see `docs/ASSETS.md`). Earlier revisions
  carried `exts/ur5e-dt/assets/`, and not all of it could be redistributed. The NVIDIA robot,
  sensor and material files, the Vention bench and the Poliigon oak textures must stay out of a
  public repository; the Husarion gripper files (Apache-2.0) and the FMB objects (CC BY 4.0) may
  ship with notices. Per-group evidence, sizes and the fetch-on-demand plan are in
  [`provenance/ASSETS.md`](provenance/ASSETS.md). The extension resolves them through
  `exts/ur5e-dt/ur5e_dt/asset_cache.py` and `exts/ur5e-dt/assets-manifest.json`;
  `scripts/fetch_ur5e_assets.py` populates the operator cache.

---

## 4. Local Patch Summary

Prior to consolidation, UR5e-specific additions to `ur5e-dt` were applied during setup via `patches/isaac-sim-mcp-ur5e-dt-owner-registrar.patch`. During repository consolidation, this patch was permanently integrated into `exts/ur5e-dt/ur5e_dt/extension.py` and the original patch file was archived to `docs/provenance/isaac-sim-mcp-ur5e-dt-owner-registrar.patch`.

Key modifications incorporated from the patch:
1. **Owner-Registrar Integration:** Added support for registering tools under an owner-bound namespace with `isaac.mcp.bridge`.
2. **Force-Callback Guard:** Guarded force sensor callback registration to prevent null-pointer crashes during rapid stage reopenings.
3. **Workstation Scene Fixes:** Hardened collider configurations for the Vention workstation, QuickChanger, and RG2 gripper; disabled gravity on the kinematic robot chain.
4. **Fixed Person-View Camera:** Added automatic creation and configuration of `/World/person_view` for consistent third-person viewing.

---

## 5. Single-Repo Consolidation Summary

All components now live directly in this repository:
- Git submodules (`.gitmodules` and `.git/modules/external`) have been eliminated entirely.
- Clones are self-contained and require no recursive submodule flags or external git network operations.
- Edits to host tools (`mcp/`) or Kit extensions (`exts/`) are tracked in the root repository history.

## Project scene implementation references

The new `sim-tools` pack and project broker are independently authored; no code was copied from
whats2000/isaacsim-mcp-server (`bfcd2111a9a98657ea87ba3be8564ea19c1a89e6`, MIT),
omni-mcp/isaac-sim-mcp (`949c4886be6e9091ceac8be4272207471b2d3d80`, MIT), or
dcc-mcp/dcc-mcp-openusd (`84595bde4f2dc87ab953688ff5a3123bcec9e88c`, MIT).
Their public sources and licenses were inspected as references. The comparison includes source-derived
signatures and descriptions from whats2000; its notice is retained in `docs/provenance/whats2000-LICENSE`. Work-r-labs/robots
(`ab6aa489855256de07ba46c7b45887fdbc42b607`, root MIT) was evaluated, but individual asset
provenance was not sufficiently established for the candidate and it was not imported or redistributed.
This does not resolve the separate omni-kit-mcp permission question above.

The live non-UR5e fixture was Agilebot GBT-C5A at
[`33281110dd75017fcb821ef4a7316960c0702e18`](https://github.com/sh-agilebot/agilebot_isaac_usd_assets/tree/33281110dd75017fcb821ef4a7316960c0702e18/gbt-c5a).
Its BSD-3-Clause license and repository THIRD_PARTY_LICENSES identify the robot as original
Agilebot work and state that former third-party gripper/camera integrations were removed.
Only the selected robot folder was imported. Robot files are not bundled; screenshots are attributed
in the evidence. The license notice is retained in `docs/provenance/agilebot-LICENSE`.
The generated multifile regression fixture is original CC0 data, not third-party robot geometry.

README pictures (`docs/media/`) are renders and screen captures made with Isaac Sim; no robot or asset files are shipped
with them. NVIDIA's Omniverse licence lets users distribute stills and screen captures they make with Omniverse
(`LICENSE.txt` in the installed `isaacsim` package, section 2.1). The Franka Panda, Nova Carter, UR5e and the Isaac Lab
G1 in the walking clip are NVIDIA Isaac Sim assets. The G1 in `g1-github-scene.png` is from
[unitreerobotics/unitree_ros](https://github.com/unitreerobotics/unitree_ros) (BSD-3-Clause, Copyright Unitree Robotics),
and the SO-ARM100 is from [TheRobotStudio/SO-ARM100](https://github.com/TheRobotStudio/SO-ARM100) (Apache-2.0). Each
picture says which in `docs/media/README.md`. This is a reading of the licences, not legal advice.
