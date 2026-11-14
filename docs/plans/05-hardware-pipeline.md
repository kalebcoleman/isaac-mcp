# Plan 05: hardware pipeline (PCB, enclosure, render) beside twinsmith

Draft. Docs only: nothing here is registered yet. It complements the roadmap and plans 01 to 04 in pull request #109
(Phase 7, "parts beyond Jenga", and decision 1, which the owner settled on 2026-11-13: `cad` is a group with this tool). No GPU is needed for the pull request; a render needs
one when it is used.

## Problem

The twin needs small custom hardware: a sensor board, a camera or gripper mount, a fixture. An agent should be able to lay
out a PCB, build its enclosure, render both, and bring the result into the scene, all without a GUI. Every step already has
a maintained tool with an MCP server or a command line. twinsmith must not rebuild them, must not grow a second CAD stack,
and must keep anything that orders, uploads or flashes out of reach.

## What runs beside twinsmith

Picked on 2026-11-13 from a survey of maintenance, license, tool coverage, ERC/DRC support and headless use. Each is a
pinned sibling checkout with its own environment under `<workspace>/tools/<name>`. Nothing is vendored, and twinsmith does
not proxy their tools: a client lists them as separate servers next to twinsmith.

| Area | Server or tool | Pin | License | Why this one |
|---|---|---|---|---|
| PCB | `kicad`: mixelpixx/KiCAD-MCP-Server | `v2.9.0` (`9dd467c`) | MIT | The only maintained, permissive server that does schematic, placement, routing (Freerouting), ERC, DRC and fab export with no KiCad GUI on KiCad 10 (SWIG backend). `KICAD_MCP_TOOLBOXES=core` lists 37 of its ~250 tools; toolboxes open on demand. Read-only and manufacturing-only modes. No code-execution tool |
| PCB checks and exports | `kicad-cli` from KiCad 10.0.8 (`ppa:kicad/kicad-10.0-releases`) | 10.0.x | GPL-3.0 | KiCad's own ERC, DRC (`--schematic-parity`), Gerber, drill, position, BOM, STEP and GLB export, all headless |
| Autorouting | Freerouting 2.5.0 (jar, Java 25 JRE in `<workspace>/tools/jre`) | 2.5.0 | GPL-3.0 | What the `kicad` server calls for `autoroute`. Analytics off with `FREEROUTING__USAGE_AND_DIAGNOSTIC_DATA__DISABLE_ANALYTICS=true`. Its online API is never used |
| Fab hand-off | KiKit `fab jlcpcb` / `fab pcbway` | 1.8.1 | MIT | The only maintained command line that writes both JLCPCB and PCBWay packages (Gerber zip, BOM, CPL with rotation fixes, `--missingError` on an empty LCSC field). No upload path |
| Parts | The `kicad` server's `jlcpcb` toolbox over a local SQLite catalogue (built from yaqwsx/jlcparts) | as the server | MIT | Offline and read-only, no key or account, no order path. `easyeda2kicad` 1.0.1 (AGPL, run as a separate command) fetches a missing symbol, footprint or 3D model by LCSC number |
| Enclosure | `freecad`: neka-nat/freecad-mcp | as installed | MIT | Already on the owner's machines. `execute_code_headless` runs `freecadcmd` in its own process, so no GUI is needed |
| Render | Blender 5.2.4 LTS, `blender -b -P script.py` | 5.2.x | GPL | Plain headless renders with Cycles OptiX (works on the RTX PRO 6000) need no add-on, port or GUI and have no time limit |
| Render, interactive | `blender`: Blender Lab `blender_mcp` (official) | `v1.0.3` (`2cea8d5`) | GPL-3.0 | The Blender Foundation's server. No telemetry. Runs against a background Blender (`--online-mode --command blender_mcp`). Its socket is localhost without authentication, so it runs only while in use |
| Firmware | `platformio`: jl-codes/platformio-mcp, PlatformIO Core 6.2.0 | `v3.1.0` (`29d4900`) | MIT | Build, native unit tests and static checks headless. Run with the `build_only` policy profile, which refuses flash and serial monitor, and with `--disable-dashboard` |

Rejected, in one line each: kicad-mcp-pro (good, but on KiCad 10 its placement and routing need a running GUI; a candidate
second, read-only reviewer); ahujasid's blender-mcp (refuses `blender -b`, telemetry on by default); ESP-IDF's
`idf.py mcp-server` (5 tools, flashing not gated); the Wokwi MCP (cloud simulation, needs a token); the PCBWay KiCad
plug-in (uploads); single-maintainer parts servers without a license.

## Design

### Group and names

One toolset, `cad`. Like every group but `core` it is off in the shipped `default`; the owner's own client configurations
turn it on (`TWINSMITH_TOOLSETS` lists it), so it is there in every session on the owner's machines. It holds **one** grouped tool with a plain name,
`hardware`. Authoring (schematic, layout, enclosure modelling, interactive Blender) stays in the beside servers above;
`hardware` only runs the deterministic steps that turn their files into checked outputs, so an agent in any client gets
the same gates. The desktop tools after pull request #108 (`doctor`, `windows`, `screenshot`, `computer`) already drive
the FreeCAD, KiCad or Blender GUI when one is wanted, so `cad` has no GUI tools.

This is decision 1 in the roadmap as the owner settled it: a group with one small tool, not a README row alone.

| Action | Runs | Notes |
|---|---|---|
| `status` | Nothing that changes state | Versions of `kicad-cli`, KiKit, `freecadcmd`, `blender`, Java and Freerouting, `pio`; the beside checkouts with their commit against the pins above; whether KiCad's global library tables exist. Never installs |
| `check` | `kicad-cli sch erc` and `kicad-cli pcb drc --schematic-parity --refill-zones`, JSON | Returns counts by severity and the first violations. Does not save the board |
| `fab` | `kikit fab jlcpcb` or `kikit fab pcbway` with `--assembly --field LCSC --missingError` | Runs `check` first and refuses with `CHECK_FAILED` on any error. Also writes `board.step` and `board.glb` with `kicad-cli`. Output goes to a new folder; never overwrites |
| `render` | `blender -b --factory-startup -P <template> -- <inputs> <out.png>` | A job (`jobs` follows and cancels it). The template is twinsmith's own short script: imports GLB, STL or STEP-converted GLB in metres, studio light, OptiX if present, CPU otherwise. GPU guard as `isaaclab__launch` |

Result envelope, as plan 03: `{ok, tool, action, versions, result | error, warnings}`.

**Into the twin.** No new tool: `board.glb` and the enclosure's STL or glTF are mesh props that `assets__import_path`
already imports ([formats](../ASSET-IMPORT.md)); plan 01's `assets__mesh` and `assets__validate` then prepare collision,
and a pack makes it reusable. STEP through Isaac Sim's CAD converter remains the Phase 7 format work.

### Safety

- No action orders, quotes, uploads or flashes. There is no `extra_args`. The subprocess environment is scrubbed as in
  plan 03 (only `PATH`, `HOME`, GPU variables and the analytics opt-out), and carries no credentials or `ROS_DOMAIN_ID`.
- Input files must be inside an approved root (the asset approval the import tool already uses); outputs go to a new
  folder under the run directory.
- `fab` output is a set of files for the owner. Sending it to a fab house is the owner's step, outside twinsmith.
- The beside `platformio` server is configured with `build_only`; its flash and monitor tools are refused by its own policy.
  The beside `blender` socket and the FreeCAD RPC port are localhost only and unauthenticated: start them only while used.

### Configuration

Executables are found on `PATH` (`kicad-cli`, `blender`, `freecadcmd`, `pio`), KiKit at
`<workspace>/tools/kikit/.venv/bin/kikit`. Two overrides only: `TWINSMITH_BLENDER` and `TWINSMITH_KIKIT`. Both go in
[ENVIRONMENT.md](../ENVIRONMENT.md).

## Files

| File | Change |
|---|---|
| `mcp/twinsmith/hardware.py` (new) | Argument building, result parsing, the registration function. About 250 lines. Uses `sibling.run` and `sibling.start_job` from plan 03; if this lands first, it adds `sibling.py` as plan 03 specifies |
| `mcp/twinsmith/hardware_render.py` (new) | The Blender template script, run inside Blender, never imported by the server. About 80 lines |
| `mcp/twinsmith/toolsets.py` | `cad` toolset, off; `_ROOTS` entry `hardware` |
| `mcp/twinsmith/server.py` | Register when `cad` is on |
| `mcp/tests/test_hardware.py` (new) | Below |
| `docs/HARDWARE.md` (new) | Setup the owner runs (the table above, install commands, client configuration for Claude Code, Codex and OpenCode), the actions, the safety rules, the KiCad 11 note |
| `docs/README.md`, `docs/TOOLSETS.md`, `docs/ENVIRONMENT.md`, `README.md` (generated) | The `cad` row and its tool |
| `docs/ARCHITECTURE.md`, `docs/ROADMAP.md` | Move `hardware` from proposed to existing |

Nothing upstream is copied, so `THIRD-PARTY-NOTICES.md` does not change.

## Tests

Fake executables are shell scripts that print their arguments as JSON or write a fixture file.

- `check` parses recorded KiCad 10 ERC and DRC JSON (committed fixtures, a few kB) into counts; a violation-free pair gives
  `ok: true`; an error gives `ok: false` and the first violations.
- `fab` runs no KiKit process when the fake `check` reports an error, and returns `CHECK_FAILED`. With a clean check it
  builds exactly `["<kikit>", "fab", "jlcpcb", "--assembly", "--schematic", <sch>, "--field", "LCSC", "--missingError",
  <pcb>, <new folder>]`; `vendor="pcbway"` swaps the subcommand; any other vendor is refused.
- `fab` refuses an existing output folder; inputs outside an approved root are refused before any process starts.
- `render` builds `["<blender>", "-b", "--factory-startup", "-P", <template>, "--", ...]` and returns a `job_id`.
- The function signature has no parameter named `extra_args`, `args`, `upload`, `order` or `port`.
- The subprocess environment has no variable whose name contains `TOKEN`, `KEY` or `SECRET`, and no `ROS_DOMAIN_ID`.
- With `cad` off nothing is registered; `cad` alone on top of `core` lists exactly `hardware`.
- `status` with nothing installed returns the missing pieces and the setup page, and starts no installer.

## Acceptance checks

1. The CI commands in the plans index pass.
2. On the ASU PC: `hardware(action="status")` reports the versions and pins above.
3. On the smoke board in `<workspace>/projects/hardware-smoke`: `check` reports 0 errors, `fab vendor="jlcpcb"` writes the
   Gerber zip, BOM and CPL plus `board.step` and `board.glb`, and `render` writes a PNG.
4. `board.glb` imports with `assets__import_path` into a scratch stage.

## Smoke test of the beside servers (2026-11-13, ASU PC)

Two fresh Claude Code sessions (Opus 5.5, high effort) used the beside servers from the owner's user configuration,
headless, with no GUI. Outputs are in `<workspace>/projects/hardware-smoke`, not in this repository.

| Step | Result | Time |
|---|---|---|
| PCB | ESP32-C3-WROOM-02, button on GPIO9, LED on GPIO8, 1x4 header, 2 x M2; 30 x 28 mm, 2 layers, routed with Freerouting. ERC 0. DRC 0 errors, 0 unconnected, 0 parity; 3 cosmetic silkscreen-to-edge warnings from the module footprint. KiKit JLCPCB Gerber zip, BOM and CPL; STEP, GLB, schematic PDF, board previews. Re-checked with `kicad-cli` outside the session | 12 min |
| Enclosure | Base and lid, 35 x 33 x 13.6 mm, 2 mm walls, 0.5 mm clearance, standoffs, header slot, LED and button holes, plastic over the antenna; FCStd, STEP, STL. Fit and interference checked against the board STEP | 2 min |
| Render | `board.png` and `assembly.png`, 1600 x 1200, Cycles OptiX, about 12 s each. The `blender` server ran against a background Blender: three tools answered, then the process was stopped | 2 min |
| Firmware | PlatformIO, `espressif32@8.0.0`, Arduino: built through `platformio` (RAM 4.4 %, flash 23 %), 6 native unit tests pass, cppcheck 0 defects. Nothing flashed or monitored | 5 min |

What it taught, and where this plan uses it:

- **Gates belong to `kicad-cli`.** The server's `run_drc` wrote text when asked for JSON, `set_design_rules` reported success
  without saving, and its courtyard checks compare bounding boxes. `check` therefore calls `kicad-cli` only.
- **Freerouting's first pass gave 54 DRC errors** (0.15 mm neck-downs, copper at the board edge, ground pads left
  unrouted because it treats GND as a plane). Fixed by board rules, edge keepouts and pre-placed ground vias. The server
  has no keepout tool; the agent used `pcbnew` directly.
- **Headless KiCad has no global library tables** (the GUI writes them on first run). `status` reports a missing
  `sym-lib-table` or `fp-lib-table` under the KiCad configuration folder; the setup page copies KiCad's templates there.
- **Parts:** the offline catalogue holds in-stock parts only; the WROOM-02 module was absent, so it was left without an
  LCSC number and out of the assembly files rather than guessed.
- **`platformio` under `build_only`** refuses `init_project` and compiles native tests without running them while
  reporting success; `pio test -e native` ran them. Its policy does not list `start_monitor` as allowed or denied.
- **`blender`'s `render_thumbnail_to_path`** wrote to a temporary folder instead of the given path.
- **Firmware and board disagreed on the LED**: the board drives it active low with a pull-up (GPIO8 is a strapping pin),
  while the firmware assumed active high. Pin assignments must come from the schematic, not be restated in a brief.

Not verified: Gerbers in a viewer or a fab design check, CPL rotations, printability, antenna performance, and anything
electrical.

## Risks

- **KiCad 11** (expected early 2027) removes the SWIG `pcbnew` module that the `kicad` server's headless backend and KiKit
  use; its headless IPC API server exists only in development builds today. Keep KiCad on 10.0.x and these pins until both
  support KiCad 11, then re-run the smoke test.
- The `kicad` server's schematic tools are its weakest part (open wiring and label issues upstream). `check` is the gate,
  and agents should look at the schematic PDF before trusting it.
- JLCPCB rotation fixes and stock in the local catalogue lag the live site; the owner re-checks before ordering.
- KiKit 1.8.1 resolves numpy 2.3 by default, which needs Python 3.11; KiCad's `pcbnew` on Ubuntu 22.04 is Python 3.10.
  Install KiKit with `numpy==2.2.6` named explicitly.

## Not in this pull request

Proxying the beside servers' tools through twinsmith, any fab upload or order path, flashing, ESP-IDF and Wokwi, STEP import
through Isaac Sim's CAD converter (Phase 7), and a KiCad 11 migration.
