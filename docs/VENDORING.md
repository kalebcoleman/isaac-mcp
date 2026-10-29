# Vendoring and Upstream Provenance

This repository consolidates two previously submodule-managed codebases into a single unified repository. This document records the provenance, upstream source repositories, fork locations, pinned commit SHAs, license status, and patch histories for all vendored components.

---

## 1. Provenance Record

| Component | Upstream Repository | Fork Repository | Pinned SHA | License Status | Local Delta / Patches |
|---|---|---|---|---|---|
| **`exts/ur5e-dt`** | `https://github.com/inbarajaldrin/isaac-sim-mcp` | `https://github.com/kalebcoleman/isaac-sim-mcp` | `27f6100` | MIT (`docs/provenance/isaac-sim-mcp-LICENSE`) | 1 file modified (`extension.py`); preserved in `docs/provenance/isaac-sim-mcp-ur5e-dt-owner-registrar.patch` |
| **`mcp/`** & **`exts/omni.kit.mcp`** | `https://github.com/inbarajaldrin/omni-kit-mcp` | `https://github.com/kalebcoleman/ur5e-mcp` | `9d0366b` | **No LICENSE file upstream** | Clean at `9d0366b` (no local patch) |

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
  - `mcp/` (host-side Python package `kit_mcp`, exposing CLI `isaac-mcp`)
  - `exts/omni.kit.mcp/` (Kit bridge extension)
  - `exts/omni.kit.mcp.panel/` (Kit bridge GUI inspector panel)
- **Local modifications:** None at the time of consolidation. Fork commit `9d0366b` vendored clean.

---

## 3. License Status

- **`isaac-sim-mcp`:** MIT License. A copy of the upstream license is archived at `docs/provenance/isaac-sim-mcp-LICENSE`.
- **`omni-kit-mcp`:** **Upstream has no LICENSE file.** The repository was published publicly on GitHub without an explicit license file. Users and redistributors should note that upstream `inbarajaldrin/omni-kit-mcp` omits an explicit license grant.

---

## 4. Local Patch Summary

Prior to consolidation, UR5e-specific additions to `ur5e-dt` were applied during setup via `patches/isaac-sim-mcp-ur5e-dt-owner-registrar.patch`. During repository consolidation, this patch was permanently integrated into `exts/ur5e-dt/ur5e_dt/extension.py` and the original patch file was archived to `docs/provenance/isaac-sim-mcp-ur5e-dt-owner-registrar.patch`.

Key modifications incorporated from the patch:
1. **Owner-Registrar Integration:** Added support for registering tools under an owner-bound namespace with `omni.kit.mcp`.
2. **Force-Callback Guard:** Guarded force sensor callback registration to prevent null-pointer crashes during rapid stage reopenings.
3. **Workstation Scene Fixes:** Hardened collider configurations for the Vention workstation, QuickChanger, and RG2 gripper; disabled gravity on the kinematic robot chain.
4. **Fixed Person-View Camera:** Added automatic creation and configuration of `/World/person_view` for consistent third-person viewing.

---

## 5. Single-Repo Consolidation Summary

All components now live directly in this repository:
- Git submodules (`.gitmodules` and `.git/modules/external`) have been eliminated entirely.
- Clones are self-contained and require no recursive submodule flags or external git network operations.
- Edits to host tools (`mcp/`) or Kit extensions (`exts/`) are tracked in the root repository history.
