"""Checks for a loose mesh file (glTF, GLB, OBJ, STL, FBX, DAE, PLY) before the converter reads it. Standard library only.

A mesh can name other files: a glTF its buffers and images, an OBJ its material library, a material its textures, a DAE its
images. They must all be inside the approved folder. A file whose references cannot be read is refused, never assumed safe.
FBX is binary and its texture paths cannot be checked here, so it is imported without materials.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from . import urdf_refs
from .project_assets import AssetError, refuse
from .urdf_refs import Project

MESH_SUFFIXES = (".gltf", ".glb", ".obj", ".stl", ".fbx", ".ply")
NO_MATERIALS = {".fbx"}  # references hidden in a binary format


def is_mesh(path) -> bool:
    return PurePosixPath(str(path)).suffix.lower() in MESH_SUFFIXES


def check_mesh_path(path) -> None:
    if PurePosixPath(str(path)).suffix.lower() == ".dae":
        refuse("UNSUPPORTED_FORMAT", f"{path} is a Collada file. Isaac Sim's asset converter cannot import .dae as a prop (it works as a mesh "
               "inside a URDF robot). Convert it to glTF or OBJ first.")
    if not is_mesh(path):
        refuse("UNSUPPORTED_FORMAT", f"Expected one of {', '.join(MESH_SUFFIXES)}, not {path}.")


def check(entry: str, project: Project) -> dict:
    """The files the mesh needs (``files``) and whether materials may be imported (``materials``). Refuses a bad reference."""
    check_mesh_path(entry)
    suffix = PurePosixPath(entry).suffix.lower()
    data = project.read(entry)
    if suffix in (".gltf", ".glb") and urdf_refs.gltf_uris(suffix, data) is None:
        refuse("MESH_XML", f"{entry}: its JSON cannot be read, so what it names cannot be checked.")
    if suffix == ".dae":
        upper = data.upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            refuse("MESH_XML", f"{entry}: DTDs and entities are not supported.")
    followed = urdf_refs.follow_mesh_files(project, [entry])
    outside, absent = [], []
    for item in followed["missing"]:
        (absent if urdf_refs.mesh_reference(item["referenced_by"], item["reference"]) else outside).append(item)
    if outside:
        names = [f"{m['reference'][:80]} (named by {m['referenced_by']})" for m in outside[:6]]
        raise AssetError("MESH_REFERENCE", "A mesh names files outside the approved folder (an absolute path, a URL, or climbing out of it): "
                         f"{'; '.join(names)}. Put them beside the mesh.", references=outside[:20])
    # A material library or texture that is simply absent is not a reason to refuse the shape: import it without materials.
    return dict(files=[entry, *followed["found"]], materials=suffix not in NO_MATERIALS and not absent, absent=[m["reference"][:80] for m in absent[:10]])
