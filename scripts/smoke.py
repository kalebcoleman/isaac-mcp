"""Run checks on disposable simulators; --list is offline and never starts Kit."""

import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

SCRIPTS = Path(__file__).resolve().parent
TOOLSETS = {
    "core": ["validate_scene_sessions"],
    "scene": ["validate_scene_tools", "validate_scene_prim_edit", "validate_scene_clear",
              "validate_scene_reopen", "validate_generic_scene"],
    "physics": ["validate_scene_joints", "validate_scene_edges", "validate_scene_equivalents", "newton_checks"],
    "sensors": ["validate_scene_sensors", "validate_scene_camera_freshness"],
    "ros2": [], "assets": [], "lab": ["smoke_test"], "screen": [], "code": [],
    "kit_debug": [], "workcell": ["workcell_smoke"],
}


# Checks that only mean something on Newton: on PhysX they are skipped, with a note, not failed.
NEWTON_ONLY = ("newton_checks",)


class BackendMismatch(RuntimeError):
    pass


def probe(client, requested):
    identity = client.call("status", {})
    reply = client.call("run_python", {"code": (
        "from isaacsim.core.simulation_manager import SimulationManager\n"
        "from sim_tools.compat import adapt_simulation_manager\n"
        "result = adapt_simulation_manager(SimulationManager).get_active_physics_engine()"
    )})
    backend = reply["result"]
    metadata = {"backend": backend, "simulator_build": identity.get("app_version"),
                "pid": identity.get("pid"), "port": client.port}
    if backend not in ("newton", "physx") or (requested and requested != backend):
        error = BackendMismatch(f"Requested {requested or 'known engine'}, but simulator reports {backend!r}; refusing to run")
        error.metadata = metadata
        raise error
    return metadata


def load_check(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / "smoke" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require(args, *names):
    missing = ["--" + name.replace("_", "-") for name in names if getattr(args, name) is None]
    if missing:
        raise ValueError("This check requires " + ", ".join(missing))


def execute(name, args, output, client_factory, metadata):
    if name in ("smoke_test", "workcell_smoke"):
        command = [sys.executable, str(SCRIPTS / f"{name}.py")]
        if name == "workcell_smoke":
            require(args, "config")
            if not args.mutate_stage:
                raise ValueError("workcell requires --mutate-stage")
            if metadata["backend"] != "physx":
                raise ValueError("Existing workcell_smoke supports PhysX only; no backend fallback")
            command += ["--host", args.host, "--port", str(args.port), "--config", args.config,
                        "--mutate-stage"]
            if args.discard_changes:
                command.append("--discard-changes")
        elif args.backend:
            raise ValueError("Lab smoke chooses its task's engine; --backend cannot be verified by this check")
        with output.with_suffix(".log").open("w") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        return
    if name == "validate_generic_scene":
        if not args.discard_changes:
            raise ValueError("Generic scene replaces the stage; requires --discard-changes")
        source = (SCRIPTS / "smoke" / f"{name}.py").read_text()
        code = f"namespace = {{'__name__': 'smoke_generic'}}\nexec({source!r}, namespace)\nresult = namespace['run_checks']()"
        with client_factory(host=args.host, port=args.port, timeout=120) as client:
            result = client.call("run_python", {"code": code})
        if result.get("result", {}).get("status") != "passed":
            raise RuntimeError(f"Generic scene did not pass: {result}")
        output.write_text(json.dumps(result, indent=2))
        return
    if name == "newton_checks":
        if not args.discard_changes:
            raise ValueError("Newton checks replace the open stage twice; requires --discard-changes")
        asyncio.run(load_check(name).validate(args.port, str(output)))
        return
    module = load_check(name)
    if name == "validate_scene_sessions":
        require(args, "second_port")
        with client_factory(host=args.host, port=args.second_port) as client:
            metadata["second_simulator"] = probe(client, args.backend)
        coroutine = module.validate(args.port, args.second_port, str(output))
    elif name == "validate_scene_reopen":
        require(args, "saved", "body", "expected_z")
        options = argparse.Namespace(**vars(args))
        options.output = str(output)
        coroutine = module.validate(options)
    elif name == "validate_scene_prim_edit":
        require(args, "directory")
        coroutine = module.validate(args.port, str(output), args.directory, args.robot)
    elif name == "validate_scene_joints":
        coroutine = module.validate(args.port, str(output), args.robot)
    elif name in ("validate_scene_tools", "validate_scene_equivalents"):
        coroutine = module.validate(args.port, str(output), backend=args.backend)
    else:
        coroutine = module.validate(args.port, str(output))
    asyncio.run(coroutine)


def main(argv=None, client_factory=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("toolset", choices=[*TOOLSETS, "all"])
    parser.add_argument("--backend", choices=("newton", "physx"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--check", action="append", help="Run only this named check from the toolset; repeatable")
    parser.add_argument("--second-port", type=int)
    parser.add_argument("--directory")
    parser.add_argument("--robot", choices=("ur5e", "franka"), default="franka")
    parser.add_argument("--saved")
    parser.add_argument("--body")
    parser.add_argument("--expected-z", type=float)
    parser.add_argument("--config")
    parser.add_argument("--mutate-stage", action="store_true")
    parser.add_argument("--discard-changes", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    selected = TOOLSETS if args.toolset == "all" else {args.toolset: TOOLSETS[args.toolset]}
    if args.list:
        print("Toolset     Check                               GPU   Running simulator")
        for toolset, checks in selected.items():
            for name in checks:
                needs_sim = "no (Lab job)" if name == "smoke_test" else "yes (two)" if name == "validate_scene_sessions" else "yes"
                print(f"{toolset:11} {name:35} yes   {needs_sim}")
            if not checks:
                print(f"{toolset:11} no checks registered                no    no")
        return 0
    checks = [name for names in selected.values() for name in names]
    if args.check:
        unknown = set(args.check) - set(checks)
        if unknown:
            parser.error(f"Checks not in this toolset: {', '.join(sorted(unknown))}")
        checks = [name for name in checks if name in args.check]
    if not checks:
        print("No checks registered for this toolset", file=sys.stderr)
        return 2
    if client_factory is None:
        sys.path.insert(0, str(SCRIPTS.parent / "mcp"))
        from isaac_mcp.client import BridgeClient
        client_factory = BridgeClient
    output_dir = args.output_dir or Path(tempfile.mkdtemp(prefix="smoke-"))
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    exit_code = 0
    previous_host = os.environ.get("ISAAC_MCP_HOST")
    os.environ["ISAAC_MCP_HOST"] = args.host
    try:
        for name in checks:
            start = time.monotonic()
            metadata = {"backend": None, "simulator_build": None}
            row = {"check": name, "status": "fail"}
            try:
                if name != "smoke_test":
                    with client_factory(host=args.host, port=args.port) as client:
                        metadata = probe(client, args.backend)
                if name in NEWTON_ONLY and metadata["backend"] != "newton":
                    row["status"] = "skipped"
                    row["note"] = f"Newton-only check; this simulator runs {metadata['backend']}, so it was not run"
                else:
                    execute(name, args, output_dir / f"{name}.json", client_factory, metadata)
                    row["status"] = "pass"
            except BackendMismatch as error:
                metadata.update(getattr(error, "metadata", {}))
                row["error"] = str(error)
                exit_code = 2
            except (Exception, SystemExit) as error:
                row["error"] = str(error)
                exit_code = 1
            row.update(metadata)
            row["seconds"] = round(time.monotonic() - start, 3)
            rows.append(row)
            if row["status"] == "fail":
                break
    finally:
        if previous_host is None:
            os.environ.pop("ISAAC_MCP_HOST", None)
        else:
            os.environ["ISAAC_MCP_HOST"] = previous_host
    for name in checks[len(rows):]:
        rows.append({"check": name, "status": "not_run", "backend": None,
                     "simulator_build": None, "seconds": 0, "error": "Stopped after preceding failure"})
    report = output_dir / "results.json"
    report.write_text(json.dumps({"toolset": args.toolset, "requested_backend": args.backend,
                                  "checks": rows}, indent=2) + "\n")
    print("Check                               Result   Backend  Build        Seconds")
    for row in rows:
        print(f"{row['check']:35} {row['status']:8} {str(row['backend']):8} {str(row['simulator_build']):12} {row['seconds']}")
        if "error" in row:
            print("  " + row["error"])
        if "note" in row:
            print("  " + row["note"])
    print(f"JSON: {report}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
