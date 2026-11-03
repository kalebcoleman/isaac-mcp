"""sim.graph.node_types / inspect / show and sim.map.occupancy: the parts that run without Kit."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("sim_tools_graph_under_test", REPO / "exts/sim-tools/sim_tools/graph.py")
graph = importlib.util.module_from_spec(spec)
spec.loader.exec_module(graph)


@pytest.fixture
def np():
    return pytest.importorskip("numpy")


def test_ogn_ports_use_og_style_names_and_keep_defaults():
    ogn = {"isaacsim.ros2.bridge.ROS2PublishClock": {
        "inputs": {"topicName": {"type": "string", "default": "clock", "description": ["Name of", "ROS2 Topic"]},
                   "execIn": {"type": "execution", "description": "The input execution port"}},
        "outputs": {}}}
    ports = graph.ogn_ports(ogn, "isaacsim.ros2.bridge.ROS2PublishClock")
    assert ports["inputs"]["inputs:topicName"] == {"type": "string", "default": "clock",
                                                   "description": "Name of ROS2 Topic"}
    assert "inputs:execIn" in ports["inputs"] and ports["outputs"] == {}


RST = '''Inputs
------
.. csv-table::
    :header: "Name", "Type", "Descripton", "Default"
    "Array Size (*inputs:arraySize*)", "``int``", "The size of the array to construct.", "1"
    "", "Metadata", "*literalOnly* = 1", ""
    "Input0 (*inputs:input0*)", "``['bool', 'colord[3]', 'double', 'float', 'half', 'int', 'int64', 'token', 'uint']``", "Input array element", "None"
Outputs
-------
    "X (*outputs:x*)", "``double``", "The x component", "None"
'''


def test_rst_ports_parse_compiled_node_docs():
    ports = graph.rst_ports(RST)
    assert ports["inputs"]["inputs:arraySize"] == {"type": "int", "default": "1",
                                                   "description": "The size of the array to construct."}
    assert ports["inputs"]["inputs:input0"]["type"].startswith("any of 9 types")
    assert ports["outputs"] == {"outputs:x": {"type": "double", "description": "The x component"}}


def test_short_bounds_values():
    assert graph._short(list(range(20)))[-1] == "... 20 items"
    assert graph._short(float("nan")) == "nan" and graph._short("x" * 500) == "x" * 200


def _buffer_with(np, cells, dims_x, dims_y):
    """Isaac buffer: row-major, dims_x per row, in Isaac's (180-degree-rotated) layout."""
    buf = np.zeros(dims_y * dims_x, dtype=np.float32)
    for r, c in cells:
        buf[r * dims_x + c] = 1.0
    return buf.tolist()


def test_map_layout_matches_the_live_probe(np):
    # Live (Isaac Sim 6.2): boxes at world (2, 1) and (-1, -2) in a [-3, 3] map landed there after
    # the 180-degree rotation. Here: one occupied buffer cell must come out at the mirrored pixel.
    dims_x, dims_y = 4, 3
    pgm, origin, counts = graph.pgm_and_origin(_buffer_with(np, [(0, 0)], dims_x, dims_y), dims_x, dims_y,
                                               (-1.0, -0.5, 0.0), 0.5, 1.0)
    header, body = pgm.split(b"255\n", 1)
    img = np.frombuffer(body, dtype=np.uint8).reshape(dims_y, dims_x)
    assert b"4 3" in header and origin == (-1.0, -0.5)
    assert img[dims_y - 1, dims_x - 1] == 0 and (img == 0).sum() == 1  # buffer (0,0) -> bottom-right pixel
    assert counts == {"occupied": 1, "free": 11, "unknown": 0}
    assert set(np.unique(img)) <= {0, 254}


def test_unknown_cells_and_units(np):
    buf = [0.5] * 4
    pgm, origin, counts = graph.pgm_and_origin(buf, 2, 2, (-100.0, -50.0, 0.0), 5.0, 0.01)  # centimetre stage
    assert origin == (-1.0, -0.5) and counts["unknown"] == 4
    assert set(pgm.split(b"255\n", 1)[1]) == {205}


def test_clear_circles_mark_the_robot_footprint_free(np):
    dims = 10
    buf = np.ones(dims * dims).tolist()  # everything occupied
    pgm, origin, counts = graph.pgm_and_origin(buf, dims, dims, (0.0, 0.0, 0.0), 0.1, 1.0, clear=[[0.5, 0.5, 0.15]])
    img = np.frombuffer(pgm.split(b"255\n", 1)[1], dtype=np.uint8).reshape(dims, dims)
    # Cells whose centres are within 0.15 m of (0.5, 0.5): the 2x2 block around it (row 4-5 from top).
    assert counts["free"] == 4 and (img[4:6, 4:6] == 254).all()


def test_contracts_registered_with_the_expected_fields():
    sys.path.insert(0, str(REPO / "mcp"))
    from isaac_mcp.scene_contracts import REGISTRY

    assert {"contains", "limit", "ports"} <= set(REGISTRY["sim.graph.node_types"]["parameters"])
    assert {"path", "limit"} <= set(REGISTRY["sim.graph.inspect"]["parameters"])
    assert {"path", "layout", "width", "height"} <= set(REGISTRY["sim.graph.show"]["parameters"])
    occ = REGISTRY["sim.map.occupancy"]["parameters"]
    assert occ["min"]["required"] and occ["max"]["required"] and occ["cell_size"]["default"] == 0.05
    assert "clear" in occ and not occ["clear"]["required"]
    assert "start" in occ and not occ["start"]["required"]


def test_rst_rows_with_escaped_quotes_are_not_dropped():
    # Real case (ArraySlice): descriptions with "" quotes made the old regex skip the row.
    text = ('    "End (*inputs:end*)", "``int``", "The end index, exclusive. ""end"" may be negative.", "0"\n'
            '    "Use Length (*inputs:useLength*)", "``bool``", "Use ""length"" instead of ""end"".", "False"\n')
    ports = graph.rst_ports(text)["inputs"]
    assert set(ports) == {"inputs:end", "inputs:useLength"}
    assert ports["inputs:end"]["description"] == 'The end index, exclusive. "end" may be negative.'


def test_find_ogn_ignores_an_empty_extension_path():
    assert graph._find_ogn("", "omni.graph.nodes.ArraySlice") is None
