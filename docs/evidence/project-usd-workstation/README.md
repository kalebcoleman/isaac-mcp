# Project USD import and workstation live test

On 2026-11-01, a real MCP client drove isolated, managed Isaac Sim sessions on a
16,303 MiB RTX 5080. The test checkout combined PR #15's implementation with PR #14's
resolver at `3a2302f`, over PR #11 at `6891967`. The tested `sim_tools/core.py` SHA-256
was `80ca1ed93c80fe90a2746501a423cd0122c8717b96410277ca1e7e45d7de89b9`.

The same workflow passed on each of the five installed 6.x packages in **both windowed
and headless mode**: `6.0.0.0`, `6.0.0.1`, `6.0.1.0`, `6.1.0.0`, and `6.2.0.0`.
These were the 6.x builds listed by [PyPI's Isaac Sim release history](https://pypi.org/project/isaacsim/)
at the time of testing. [MATRIX.json](MATRIX.json) records build identities, checks, camera
results and resource snapshots for all ten runs. This is coverage of the specified workflow
and fixtures on this Linux/NVIDIA machine, not a guarantee for every asset, configuration,
platform or future release.

A separate 6.0.0 simulator was already running. The MCP `sim__launch` tool started this
second process with a distinct bridge port, ROS domain, output directory, Kit settings,
asset cache, and trusted-project staging. Its standard two-instance limit and free-memory
checks remained enabled: about 11,344 MiB of VRAM was free at launch and about 9,391 MiB
was free after startup. The original process remained running when that test process was
stopped through `sim__stop`. Subsequent tests used distinct ports and one additional process
at a time. A just-closed port remained in TCP TIME_WAIT, so its launch was refused; a fresh
port succeeded. These snapshots are not memory reservations or proof that other pairs of
scenes will fit.

The client selected the new bridge, checked its PID and stage tokens, then:

1. Imported a generated, layered USD containing a textured mesh using `assets__import_path`.
   The prim tree contained the mesh and its material/shaders, and the shader's asset path
   resolved into the private project snapshot. The generated red/blue checker texture was
   visibly rendered in the [camera image](generic-texture.png), SHA-256
   `d80380e0e5707f0ad6ba4b40a33d94495bcc1b151db7f28f639027a8a8c4a12d`. The image was
   captured in the final retest with the runtime MDL fix below. A separate
   [6.2 window capture](windowed-6.2.png) shows the same generated fixture in the actual
   simulator UI; all five windowed screenshots were inspected.
2. Submitted a USD whose MaterialX file referenced a missing texture. Every run's
   `assets__import_path` call reported the missing dependency and verified that its requested
   prim was not created. In a separate 6.2 check, `assets__import_usd` also rejected that
   missing dependency. A valid root-ID import succeeded in every matrix run.
3. Prepared an operator-supplied Vention USD project in an empty private cache with
   `assets__prepare_workstation`. It reported five copied project files, three MDL texture
   paths, a checked installed `OmniPBR.mdl`, `prepared: true` and `loaded: false`. The
   original USD file's SHA-256 was unchanged. A repeat preparation refused to overwrite the
   existing cache entry.
4. Called `ur5e_dt__load_workstation` and confirmed the composed stage used the new cache
   wrapper and copied project, had no composition errors, and placed the tabletop at z≈0.
   A stage inspection found 89 geometry prims with bound materials and no unresolved paths
   among 91 authored asset-valued attributes in the 6.2 inspection. The bench was rendered
   and visually inspected on 6.2; that operator-supplied asset and its image are intentionally
   not included in the repo.

The first preparation attempt exposed a bug: Kit reported its installed OmniPBR module as a
resolved asset, while the importer rejected it as outside the snapshot. The retest above used
the fix, which recognizes only a registered installed runtime file and rechecks its hash.
All ten newly created matrix cameras returned `IHydraTexture::getFrameData for 0 failed` on their
first capture; retries three seconds later produced fresh frames. That separate camera issue was
reported to the PR #11 work. Kit 6.2 logged a Fabric warning about a missing `material:binding` attribute on a
`Looks` scope while loading the bench; the 89 geometry material bindings described above were
present, but this check does not establish full visual equivalence or perfect material behavior.

These are trusted local assets. The tests do not establish isolation from untrusted USD,
network/resolver behavior, remote transfers, physical-robot behavior, or completeness for
arbitrary material systems. See [tool limitations](../../TOOLS-SCENE.md#project-usd-and-the-ur5e-workstation).
