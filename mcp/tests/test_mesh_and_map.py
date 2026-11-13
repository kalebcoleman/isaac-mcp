"""Loose meshes and ROS maps: the checks that run on the host or without Kit."""

import json
import struct
import sys
from pathlib import Path

import pytest

from twinsmith import mesh_refs
from twinsmith.project_assets import AssetError
from twinsmith.urdf_refs import Project

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "exts" / "sim-tools"))


def project(files):
    data = {k: (v if isinstance(v, bytes) else v.encode()) for k, v in files.items()}
    return Project(data.keys(), lambda rel: data[rel], "root")


def gltf(**refs):
    return json.dumps({"asset": {"version": "2.0"}, "buffers": [{"uri": u} for u in refs.get("buffers", [])], "images": [{"uri": u} for u in refs.get("images", [])]})


def test_a_gltf_with_its_files_beside_it_is_accepted_with_materials():
    found = mesh_refs.check("a/duck.gltf", project({"a/duck.gltf": gltf(buffers=["d.bin"], images=["t.png"]), "a/d.bin": b"x", "a/t.png": b"x"}))
    assert found["materials"] is True and found["files"] == ["a/duck.gltf", "a/d.bin", "a/t.png"] or set(found["files"]) == {"a/duck.gltf", "a/d.bin", "a/t.png"}


@pytest.mark.parametrize("uri", ["../../../../etc/passwd", "file:///etc/hosts", "/etc/passwd", "http://example.com/t.png", "a\\\\b.png", "../../up.png"])
def test_a_gltf_that_names_a_file_outside_the_folder_is_refused(uri):
    with pytest.raises(AssetError) as caught:
        mesh_refs.check("a/m.gltf", project({"a/m.gltf": gltf(images=[uri])}))
    assert caught.value.code == "MESH_REFERENCE"


def test_a_missing_texture_or_material_library_is_a_warning_not_a_refusal():
    found = mesh_refs.check("m.obj", project({"m.obj": "mtllib gone.mtl\nv 0 0 0\n"}))
    assert found["materials"] is False and found["absent"] == ["gone.mtl"]


def test_embedded_data_is_fine_and_unreadable_json_is_refused():
    assert mesh_refs.check("m.gltf", project({"m.gltf": gltf(buffers=["data:application/octet-stream;base64,AAAA"])}))["materials"] is True
    with pytest.raises(AssetError):
        mesh_refs.check("m.gltf", project({"m.gltf": "{not json"}))
    glb = b"glTF" + struct.pack("<II", 2, 24) + struct.pack("<I", 4) + b"BIN\x00" + b"abcd"
    with pytest.raises(AssetError):
        mesh_refs.check("m.glb", project({"m.glb": glb}))


def test_fbx_comes_in_without_materials_dae_is_explained_and_other_files_are_refused():
    assert mesh_refs.check("m.fbx", project({"m.fbx": b"Kaydara FBX Binary"}))["materials"] is False
    for name, hint in (("m.dae", "Collada"), ("m.txt", "Expected")):
        with pytest.raises(AssetError) as caught:
            mesh_refs.check_mesh_path(name)
        assert hint in str(caught.value) and caught.value.code == "UNSUPPORTED_FORMAT"


def test_ros_map_cells_become_few_boxes():
    from sim_tools import map_import

    data = b"P5\n# comment\n4 3\n255\n" + bytes([0, 0, 255, 255, 0, 0, 255, 0, 255, 255, 255, 0])
    width, height, maxval, pixels = map_import.read_pgm(data)
    assert (width, height, maxval) == (4, 3, 255)
    assert sorted(map_import.rectangles([v < 100 for v in pixels], width, height)) == [(0, 0, 2, 2), (3, 1, 1, 2)]
    with pytest.raises(ValueError):
        map_import.read_pgm(b"P5\n2 2\n255\nab")  # truncated
    plain = b"P2\n2 1\n255\n0 255\n"
    assert map_import.read_pgm(plain)[3] == [0, 255]
