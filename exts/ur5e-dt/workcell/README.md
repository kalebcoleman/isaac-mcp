# UR5e Jenga work cell BOM and private asset pack

Status: **candidate, not runtime-qualified.** The pack-backed Workcell API is integrated
at baseline `aff668a` on `workcell/lab-hardware`. Integration and CPU fixture checks do
not establish a PhysX, render, sensor or lab-fidelity pass. This documentation update
does not run the simulator or inspect private assets.

## Scope

The work cell is one UR5e on a Vention bench with a Bota Rokubi, OnRobot Quick Changer,
standard OnRobot RG2, black finger pads, a wrist D415 and an overhead D415 on a removable
gantry. The Jenga tower is 54 blocks of 72 x 24 x 14 mm.

Machine-readable data:

- BOM: `<repo>/exts/ur5e-dt/workcell/bom.csv`
- Human-readable BOM: [BOM.md](BOM.md) (the same 13 logical rows and quantities)
- Hardware configuration: `<repo>/exts/ur5e-dt/config/lab_hardware.json`
- Audit helper: `<repo>/exts/ur5e-dt/ur5e_dt/bom.py`
- Audit test: `<repo>/mcp/tests/test_workcell_bom.py`

## Pack-backed Workcell API

The baseline exposes seven grouped tools with nine actions through `isaac.mcp.bridge`:

| Group | Actions | Purpose |
| --- | --- | --- |
| `ur5e_dt__workcell` | `quick_start`, `status`, `shutdown` | Build a stopped pack-backed scene, read its report, or release workcell state without clearing the stage |
| `ur5e_dt__robot` | `status` | Read the robot build report |
| `ur5e_dt__perception` | `status` | Read camera configuration, not captured sensor data |
| `ur5e_dt__jenga` | `status` | Read the tower build report |
| `ur5e_dt__real2sim` | `status` | Report synchronization as not implemented |
| `ur5e_dt__planning` | `status` | Report planning as not implemented |
| `ur5e_dt__eval` | `status` | Report evaluation as not implemented |

`quick_start` accepts `config_path`, `mode` (`physics` or `mirror_real`) and
`discard_changes`. Physics mode uses gravity; mirror mode uses zero gravity, not a
real-robot connection. Replacing an unsaved stage requires explicit consent. There is no
extension panel or legacy ROS graph setup, and no implemented force capture, planning,
real-hardware control or evaluation. Generic scene tools remain separate. See the
[tool reference](../../../docs/TOOLS-UR5E.md) for the baseline contract.

A seeded Jenga build action is pending integration by the lead; it is not part of this
nine-action baseline. The planned variation concept is reproducible, seeded variation
around nominal blocks and tower placement, with physical uncertainty kept distinct from
confirmed nominal dimensions. The exact variation schema and supported ranges are pending
and will be supplied by the lead; no configuration keys or runtime guarantees are defined here.

## Hardware configuration and provenance

`lab_hardware.json` is the single hardware configuration. Every leaf is
`{value, status, source}`. The BOM does not replace it and does not silently choose between
conflicting measurements.

| Status | Meaning |
| --- | --- |
| `confirmed` | Measured, or stated by the lab owner or a datasheet |
| `unconfirmed` | Best current value; must be measured before fabrication or fidelity claims |
| `conflict` | Sources disagree; the configured value is the default, alternatives are recorded |
| `placeholder` | Stand-in geometry or physics value, not a hardware statement |
| `to_buy` | Item is not in the lab; the value is a design choice |

The pack-backed work-cell configuration selects the pack by `pack.id` and `pack.version`
and maps every physical scene role to a **logical asset ID** under `assets`. No asset file
path is stored in the configuration, and there is no old-store path fallback.

## BOM IDs and quantities

`bom.csv` uses one row per logical asset and instance counts in `quantity`. Every simulated
physical part must map to one of these rows.

| Area | BOM IDs and quantity |
| --- | --- |
| Scene | `bench` 1 |
| Robot | `robot` 1, `base_plate` 1, `bota` 1, `quick_changer` 1, `rg2` 1, `finger_pad` 2 |
| Cameras | `d415` 2, `wrist_mount` 1, `overhead_mount` 1, `overhead_release` 1, `overhead_shelf` 1 |
| Jenga | `jenga_block` 54 |

Both cameras use the single `d415` logical asset; the BOM records two physical units. The
two finger pads use the single `finger_pad` logical asset. A partial tower may instantiate
fewer blocks than the BOM quantity, but each instantiated block maps to `jenga_block`.

## Pricing

`bom.csv` separates money from hardware provenance:

- `price_status=verified_web_list`: a public manufacturer or store list price captured on
  2026-11-12 with its URL in `price_source`. It is a list price, not a quote.
- `price_status=quote_required`: no verified public price for the required configuration.
  `unit_price` is intentionally blank, never zero and never invented.

The only known item prices recorded for this BOM are the D415 body (272.00 USD each,
two units) and the Thorlabs KB3X3 release (117.27 USD, one unit). These are recorded
list prices, not newly checked prices or procurement quotes. Every other row, including
the complete overhead gantry, remains `quote_required`. No complete workcell total is
available; missing prices are not zero.

No purchase, order or financial action is performed by this documentation or its tests.

## Private asset pack

The pack is external to the repository:

- Pack ID and version: `ur5e-jenga-workcell/v1`
- Default root: `<home>/isaac-workspace/isaac-mcp-assets`
- Pack directory: `<home>/isaac-workspace/isaac-mcp-assets/ur5e-jenga-workcell/v1`
- Override: set `ISAAC_MCP_ASSET_ROOT` to a different asset root; the loader appends the
  pack ID and version.

All pack binaries are private, untracked lab data. They are not committed to the
repository and are not redistributable.

`pack.yaml` is JSON-compatible YAML with this shape:

```yaml
id: ur5e-jenga-workcell
version: v1
qualification: candidate; no PhysX, render, or lab-fidelity pass yet
files:
  - asset_id: robot
    path: robot/robot.usdc
    sha256: <64 lowercase hex characters>
    source: <where the CAD came from and its source hash>
    license: <license or private-use statement>
    redistributable: false
    fidelity: <what is genuine, flattened or proxied>
```

`asset_id` is optional in the schema and defaults to the parent directory name. The current
pack declares it for every file. Every referenced file needs `path`, `sha256`, `source`,
`license` and a boolean `redistributable`.

Loader contract:

- `asset_pack.AssetPack.asset(logical_id)` returns the absolute path for a logical ID.
- `asset_pack.resolve_pack(config)` resolves the configured pack ID and version.
- `asset_pack.verify_dependencies(pack)` reports the dependency closure and unresolved
  references.

The scene integration uses those functions. It must not fall back to an old store, a
previous checkout or a path in the configuration.

## Making or updating a pack

1. Work from a lab-authorized source copy. Do not delete or move an existing asset store.
2. Flatten the chosen USD, remove instanceable prototypes, keep the default prim and set
   metres, kilograms and Z-up. Replace proprietary MDL materials with USD Preview Surface
   materials when the MDL cannot ship in the private pack.
3. Remove external asset references that cannot be resolved inside the pack. The final
   file must be self-contained.
4. Write the file under
   `<root>/ur5e-jenga-workcell/v1/<asset_id>/<asset_id>.usdc` (or `.usda`).
5. Hash the final bytes with SHA-256 and record the source hash, license and
   `redistributable` flag in `pack.yaml`.
6. Run the dependency closure check. Each file should report one layer, no external asset
   references and no unresolved references. A dependency failure blocks the pack; it is not
   worked around with a fallback path.
7. Load the pack through `AssetPack.asset` and `resolve_pack`, then run the BOM audit and the
   PhysX smoke before claiming runtime qualification or making any old-store report.

The BOM audit checks structural manifest fields and, when given a pack root, the actual file
hashes. It also requires every non-`materials` pack asset to have a BOM row and every BOM
asset to exist in the pack.

## Physical fidelity

The current candidate contains:

- Genuine UR5e, Vention bench, OnRobot Quick Changer and OnRobot RG2 meshes, flattened from
  preserved local vendor CAD. Their MDL materials were replaced with preview materials, so
  appearance is not vendor-accurate.
- No genuine Bota Rokubi, D415, wrist bracket or gantry CAD is available. These are
  dimensioned, unconfirmed proxies.
- The planned overhead support is a three-member gantry (two posts and a cross-member),
  not a slab. The cantilever and real attachment details are missing or unconfirmed;
  proxy geometry does not establish mounting feasibility. The KB3X3 is a camera-only
  release, not a load-bearing gantry base. Printed mounts and shelf remain proxies.
- A plain Jenga block proxy without measured chamfers.

This is not a high-fidelity-qualified model. Flattening preserves mesh and articulation
layout from the vendor files, but material appearance, collision behavior, mass properties
and alignment still require validation. Proxy dimensions come from the hardware
configuration or a design envelope and are not vendor drawings.

## Measurements still needed

| Item | Configured value | Status |
| --- | --- | --- |
| Bench height | 0.885 m default; 0.801 m alternative | `conflict` |
| Bench top | 1.4 x 0.9 m | `unconfirmed` |
| Base plate thickness | 0.005 m (5 mm), replacing the old zero placeholder | Provisional; installed thickness `unconfirmed` |
| Bota diameter / length / mass | 0.048 m / 0.032 m / 0.12 kg | `unconfirmed` |
| Quick Changer standoff | 0.01592 m (15.92 mm) in CAD | CAD `confirmed`; installed size `unconfirmed` |
| Quick Changer mass | 0.41 kg | `unconfirmed` |
| RG2 maximum opening | 0.110 m | `confirmed` |
| Finger pad size | 0.012 x 0.008 x 0.025 m | Provisional proxy; installed dimensions `unconfirmed` |
| D415 body | 0.099 x 0.020 x 0.023 m | `unconfirmed` |
| Wrist camera offset | -0.012, -0.038, -0.005 m in tool0 | `conflict` |
| Overhead camera position | 0.7, -0.1333, 1.02 m | `unconfirmed` |
| Gantry post positions and height | 1.05, +/-0.55 m; 1.1 m | `unconfirmed` |
| Printed shelf | 0.1 x 0.06 x 0.008 m | `placeholder` |
| Jenga nominal block size | 0.072 x 0.024 x 0.014 m (72 x 24 x 14 mm) | Nominal dimensions `confirmed`; tolerances `unconfirmed` |
| Jenga block mass and friction | Not measured | `unconfirmed` |
| Jenga tower | 54 blocks, 18 layers | `confirmed` |

The lead's pending CSV updates distinguish the provisional 5 mm plate and confirmed
15.92 mm Quick Changer CAD standoff from unmeasured installed hardware. Older CSV notes
may still show a zero plate or an unqualified standoff confirmation until those updates
land; this documentation does not edit the CSV. Confirmed nominal Jenga dimensions do not
confirm manufacturing tolerances, mass, friction or chamfers.

Measure the bench, base plate, Bota envelope and mass, Quick Changer installed size and mass, pad geometry,
camera offsets and gantry before calling the model dimensionally faithful.

## BOM audit

`ur5e_dt.bom.audit_parts(parts, rows, stage)` accepts a report's `parts` list and an
optional USD stage. It checks that:

- every part has a nonempty `prim_path`, `asset_id` and `bom_id`;
- the BOM row exists and its logical `asset_id` matches the report;
- a supplied stage has the part prim and matching `workcell:asset_id` and
  `workcell:bom_id` attributes;
- every mesh, collider or rigid-body prim has a mapped BOM ancestor, including camera mount
  pieces and every Jenga block instance;
- every BOM row has at least one mapped physical part.

The audit walks the stage instead of trusting a manually curated list. A mesh anywhere
outside a mapped part, or a report part whose prim is missing, fails the audit.

## PhysX smoke

The release target is PhysX. Run the smoke only against an explicitly selected bridge and
configuration:

```bash
python3 <repo>/scripts/workcell_smoke.py --host 127.0.0.1 --port <port> \
  --config <absolute-config-path> --mutate-stage
```

`--config` must be absolute on the Kit host. `--discard-changes` is allowed only with
explicit authorization because the smoke replaces the stage. The smoke checks the backend,
stopped timeline, units, gravity, Jenga layout and per-part attributes. Pair it with the BOM
audit on the returned parts before claiming a pass.

## Current status and gaps

- Earlier pack preparation reported 14 declared files with checked manifest fields,
  hashes and dependency closure. This documentation update does not recheck that private
  pack; those structural results do not establish a PhysX or render pass.
- The integrated baseline includes a BOM contract and stage-mapping audit with CPU
  fixtures. No real-stage BOM audit, PhysX smoke or render pass is claimed here.
- No genuine Bota, D415 or mount CAD is available; those parts remain unconfirmed proxies.
- The old-store unreferenced report must wait until the pack loads, PhysX smoke passes,
  renders are reviewed and the BOM audit passes on the real stage. It is intentionally not
  produced here.
