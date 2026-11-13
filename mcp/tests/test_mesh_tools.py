"""assets__mesh and assets__validate through the server's dispatch: paths, refusals, the missing extra, a job."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

pytest.importorskip("pxr")
pytest.importorskip("trimesh")

import mesh_fixture  # noqa: E402

from twinsmith import mesh_worker  # noqa: E402
from twinsmith import pack_manifest as pm  # noqa: E402
from twinsmith import server as gw  # noqa: E402
from twinsmith.isaaclab import jobs  # noqa: E402
from twinsmith.project_assets import Broker  # noqa: E402

ENV = ("TWINSMITH_ASSET_ROOT", "TWINSMITH_WORKSPACE", "TWINSMITH_PROJECT", "TWINSMITH_USD_PYTHON", *pm.REMOVED_ENVIRONMENT)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(pm, "_announced", set())
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TWINSMITH_PROJECT", str(tmp_path / "no-project"))
    monkeypatch.setenv("TWINSMITH_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setattr(jobs, "_probe_kit_bridges", lambda: [])
    assets = tmp_path / "assets"
    assets.mkdir()
    monkeypatch.setenv("TWINSMITH_ASSET_ROOT", str(assets))
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    config = tmp_path / "approval.json"
    config.write_text(json.dumps(dict(version=1, profile="trusted_project", roots={"demo": str(project)}, staging=str(staging))))
    state = gw._BridgeState()
    state.asset_broker = Broker(str(config))
    source = project / "src" / "bench.usdc"
    mesh_fixture.build(source)
    return dict(state=state, project=project, assets=assets, source=source, tmp=tmp_path)


def call(setup, canonical, **params):
    return json.loads(gw._dispatch(canonical, params, setup["state"]))


def test_inspect_and_validate_answer_directly(setup):
    report = call(setup, "assets.mesh_inspect", source_path=str(setup["source"]))
    assert report["status"] == "ok" and report["totals"]["triangle_mesh_collider_triangles"] > 19_000
    check = call(setup, "assets.validate", source_path=str(setup["source"]), triangle_budget=5000)
    assert check["status"] == "ok" and check["ok"] is False and check["engine"] == "newton"
    assert call(setup, "assets.validate", source_path=str(setup["source"]), kit=True)["code"] == "NOT_AVAILABLE"


def test_a_pack_asset_is_read_by_id(setup):
    directory = setup["assets"] / "cell" / "v1"
    (directory / "bench").mkdir(parents=True)
    data = setup["source"].read_bytes()
    (directory / "bench" / "bench.usdc").write_bytes(data)
    (directory / "pack.yaml").write_text(json.dumps(dict(id="cell", version="v1", files=[dict(
        asset_id="bench", path="bench/bench.usdc", sha256=hashlib.sha256(data).hexdigest(), source="test",
        license="test", redistributable=False)])))
    report = call(setup, "assets.mesh_inspect", pack="cell/v1", asset_id="bench")
    assert report["status"] == "ok" and report["file"].endswith("cell/v1/bench/bench.usdc")
    assert call(setup, "assets.mesh_inspect", pack="cell/v1", asset_id="nope")["code"] == "PACK_NOT_VERIFIED"


def test_paths_outside_roots_and_bad_outputs_are_refused(setup):
    outside = setup["tmp"] / "outside.usdc"
    outside.write_bytes(setup["source"].read_bytes())
    assert call(setup, "assets.mesh_inspect", source_path=str(outside))["code"] == "ROOT_NOT_APPROVED"
    assert call(setup, "assets.mesh_inspect")["code"] == "INVALID_VALUE"
    src = str(setup["source"])
    assert call(setup, "assets.mesh_split", source_path=src, output=str(setup["tmp"] / "x"))["code"] == "ROOT_NOT_APPROVED"
    assert call(setup, "assets.mesh_split", source_path=src, output=str(setup["project"] / "src"))["code"] == "OUTPUT_EXISTS"
    assert call(setup, "assets.mesh_split", source_path=src, output=str(setup["assets"] / "new"))["code"] == "INVALID_PATH"
    assert call(setup, "assets.mesh_hulls", source_path=src, output=str(setup["project"] / "h"), method="sphere")["code"] == "INVALID_VALUE"
    assert call(setup, "assets.mesh_decimate", source_path=src, output=str(setup["project"] / "d"))["code"] == "INVALID_VALUE"


def test_missing_extra_is_named_with_the_install_command(setup, monkeypatch):
    monkeypatch.setattr(mesh_worker, "missing_modules", lambda: ["coacd"])
    result = call(setup, "assets.mesh_inspect", source_path=str(setup["source"]))
    assert result["code"] == "MESH_EXTRA_MISSING" and "--extra mesh" in result["message"] and "coacd" in result["message"]


def test_split_runs_as_a_job_and_writes_a_report(setup):
    out = setup["project"] / "staging-bench"
    started = call(setup, "assets.mesh_split", source_path=str(setup["source"]), output=str(out), collision={"method": "box"})
    assert started["status"] == "ok" and started["job_id"]
    deadline = time.time() + 120
    while time.time() < deadline:
        state = jobs.runs_status(started["job_id"])
        if state["state"] not in ("running", "starting", "queued"):
            break
        time.sleep(0.2)
    assert state["state"] == "succeeded", state
    report = json.loads((out / mesh_worker.REPORT).read_text())
    assert report["action"] == "split" and Path(report["output"]) == out / "bench.usda"
    assert {p.name for p in out.iterdir()} == {"bench.usda", "bench_render.usdc", "bench_collision.usdc", mesh_worker.REPORT}


def test_listed_schemas_take_prim_paths_as_strings():
    import asyncio

    tools = {t.name: t for t in asyncio.run(gw.mcp.list_tools())}
    for name in ("assets__mesh", "assets__validate"):
        assert name in tools
    assert tools["assets__mesh"].inputSchema["properties"]["prims"]["items"] == {"type": "string"}
    assert set(tools["assets__mesh"].inputSchema["properties"]["action"]["enum"]) >= {"inspect", "decimate", "hulls", "split"}
