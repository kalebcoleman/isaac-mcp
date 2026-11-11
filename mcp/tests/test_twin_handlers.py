"""Twin handler messages: a placeholder is not Vention, and asset refusals carry no traceback."""

import ast
import asyncio
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest
import twin_source


ROOT = Path(__file__).resolve().parents[2]


def _twin_function(name):
    tree = ast.parse(twin_source.SOURCE.read_text())
    return next(n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


class AssetMissing(FileNotFoundError):
    pass


class AssetCorrupt(FileNotFoundError):
    pass


def _twin_namespace(*names):
    module = ast.fix_missing_locations(ast.Module(body=[_twin_function(n) for n in names], type_ignores=[]))
    namespace = {"Dict": dict, "Any": object, "traceback": traceback, "AssetMissing": AssetMissing,
                 "AssetCorrupt": AssetCorrupt, "carb": SimpleNamespace(log_error=lambda *_: None)}
    exec(compile(module, "extension.py", "exec"), namespace)
    return namespace


def test_load_workstation_message_does_not_claim_vention_for_a_placeholder():
    ns = _twin_namespace("_cmd_load_workstation")

    async def load():
        return None

    for label in ("a placeholder bench (no Vention workstation asset)", "the Vention workstation"):
        owner = SimpleNamespace(load_workstation=load, _bench_label=lambda label=label: label)
        result = asyncio.run(ns["_cmd_load_workstation"](owner))
        assert result["status"] == "success" and result["message"].startswith(f"Loaded {label} at /World/Vention")


@pytest.mark.parametrize("handler, method", [
    ("_cmd_load_workstation", "load_workstation"),
    ("_cmd_quick_start_with_workstation", "quick_start_with_workstation"),
    ("_cmd_load_simple_room", "load_simple_room"),
])
def test_asset_refusals_carry_no_traceback_but_bugs_do(handler, method):
    ns = _twin_namespace(handler, "_failure_detail")

    def result_for(exc):
        async def fail():
            raise exc

        return asyncio.run(ns[handler](SimpleNamespace(**{method: fail})))

    refused = result_for(AssetMissing("add it with isaac_assets.py"))
    assert refused["status"] == "error" and "isaac_assets.py" in refused["message"] and "traceback" not in refused
    assert "AssetCorrupt" in result_for(AssetCorrupt("hash differs"))["traceback"]
    assert "ZeroDivisionError" in result_for(ZeroDivisionError("bug"))["traceback"]
