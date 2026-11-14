# ASU asset inventory — 2026-11-12

The canonical checkout is `<workspace>/twinsmith`; the asset pack is
`<workspace>/assets/ur5e-jenga-workcell/v1`. No external assets or old checkouts were deleted. Version `v2` (2026-11-13) sits
next to it and is what the work cell loads; see [the README](README.md#pack-v2).

## Checkout inventory

| Location | Finding |
| --- | --- |
| `<home>/Desktop/twinsmith` | Absent |
| `<home>/Desktop/ur5e-digital-twin` | Absent |
| `<home>/src/twinsmith` | Absent |
| `<workspace>/projects/ur5e-digital-twin` | Separate project; retained |
| the workspace's salvage folder | Historical patches and recovery material; retained |

The project checkout is not a disposable duplicate. Its local changes and branches are
recorded in the machine-local inventory JSON under `<workspace>/runs/workcell-lab-hardware`.

## Asset migration and repair

`python3 scripts/assets.py migrate --no-legacy-stores` reported
**0 added, 147 already present**: Aldrin-derived lab assets were already migrated.

The original lab robot USD was copied (not moved) to
`<workspace>/archive/2026-11-12/workcell-store/ur5e.usd.pre-robotiq-fix`.
Its dangling unused `Robotiq_2f_85` variant and empty Gripper overrides were removed
from the active local robot USD; the local pack was re-pinned. The remaining
`OmniPBR.mdl` reference resolves through Kit and is not a missing local robot file.

The NVIDIA manifest now uses the Isaac **6.2** asset root. All fetch entries were
downloaded and hashed; three changed versus 5.1 (UR5e entry, UR5e base layer, D455).
The D455 remains a store/fetch compatibility asset, not a D415 substitute in the work cell.
The 6.2 `rsd415.usd` URL returned 404; D415 body/bracket geometry is explicitly approximate.

## Preserved cache differences

Two old uv git cache checkouts were inspected: `7ff4f70` and `41eb09f`.
Each has 144 byte-identical files versus the active store. They were not moved
because they are package-manager caches and contain unique older files.

Source-only files:
- `sensors/rsd455.usd`
- `sensors/SubUSDs/textures/Aluminum_Cast/Aluminum_Cast_N.png`
- `sensors/SubUSDs/textures/Aluminum_Cast/Aluminum_Cast_BaseColor.png`
- `sensors/SubUSDs/textures/Aluminum_Cast/Aluminum_Cast_ORM.png`
- `sensors/SubUSDs/textures/Aluminum_Anodized/Aluminum_Anodized_Normal.png`
- `sensors/SubUSDs/textures/Aluminum_Anodized/Aluminum_Anodized_ORM.png`
- `sensors/SubUSDs/textures/Aluminum_Anodized/Aluminum_Anodized_BaseColor.png`
- `sensors/SubUSDs/materials/Aluminum_Cast.mdl`
- `sensors/SubUSDs/materials/Plastic_ABS.mdl`
- `sensors/SubUSDs/materials/Aluminum_Anodized.mdl`
- `gripper/RG2.usd`
- `gripper/husarion_rg2/husarion_rg2_fixed.usd`
- `objects/fmb4/materials/oak/Poliigon_WoodVeneerOak_7760_Preview1.png`
- `objects/fmb4/materials/oak/Poliigon_WoodVeneerOak_7760_Metallic.jpg`
- `objects/fmb4/materials/oak/Poliigon_WoodVeneerOak_7760_Roughness.jpg`
- `objects/fmb4/materials/oak/Poliigon_WoodVeneerOak_7760_BaseColor.jpg`
- `objects/fmb4/materials/oak/Poliigon_WoodVeneerOak_7760_ORM.jpg`
- `objects/fmb4/materials/oak/Poliigon_WoodVeneerOak_7760_Normal.jpg`
- `objects/fmb4/materials/oak/Poliigon_WoodVeneerOak_7760.mtlx`
- `objects/fmb4/materials/oak/Poliigon_WoodVeneerOak_7760_AmbientOcclusion.jpg`

Different files (active versions preserved):
- `objects/fmb1/u_orange.usda`
- `objects/fmb1/u_brown.usda`
- `objects/fmb1/base1.usda`
- `objects/fmb1/line_green.usda`
- `objects/fmb1/inverted_u_yellow.usda`
- `objects/fmb4/base4.usda`
- `objects/fmb4/jenga_01.usda`
- `objects/fmb4/jenga_06.usda`
- `objects/fmb4/jenga_05.usda`
- `objects/fmb4/jenga_03.usda`
- `objects/fmb4/jenga_04.usda`
- `robot/ur5e.usd`

The cache includes older gripper alternatives and oak textures. These are inventory
findings, not endorsements of their provenance or redistribution rights. No asset
binaries or textures are committed in this change. Marker and non-Jenga assets
remain outside the active scene.

## Source pins

- Max `RISL_camera_localizer`: `76b68e87b1ad69eec567c250b233eff94284f46a` (source read only; no hardware code executed).
- Max `RISL_onrobot_ros`: `7f86a05a8e2ba5266a2db91119b3ced6d720d2d9` (source read only; no hardware code executed).
- Max `RISL_simulation`: `d659f7d4a3f79b26829d08cfe5b12f07a1b3806d` (source read only; no hardware code executed).
- Max `RISL_ur`: `011539abcacca4da7252227c6723f786e41317e3` (source read only; no hardware code executed).
- Max `risl_assembly_workspace`: `82b9a510592c362ff9d3268754afcaebbc75bba2` (source read only; no hardware code executed).
