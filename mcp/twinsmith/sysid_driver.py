"""Fit one dataset with MuJoCo's system identification module and write ``fit.json``.

Runs only in the sysid environment's own interpreter (``<workspace>/tools/sysid/.venv``), started by the
``sysid`` tool as a job. It is the only file in twinsmith that imports ``mujoco``; the host checks the dataset
before starting it. The steps follow ``mujoco.sysid``'s own integration test: one ``ModelSequences`` for the
fit episodes (another scores the held-out ones), ``build_residual_fn``, then ``optimize`` with the ``mujoco`` least-squares backend.

    python driver.py <fit folder>/request.json

``request.json`` is written by the host: the dataset folder, the parsed parameters, the held-out episodes and
the iteration limit. ``fit.json`` goes beside it.
"""

import os
import sys

if not sys.flags.isolated:  # never the server's PYTHONPATH, PYTHONHOME or user site: the environment status checked
    os.execv(sys.executable, [sys.executable, "-I", *sys.argv])

import argparse
import hashlib
import json
import math
import time
from pathlib import Path

import mujoco
import numpy as np
from mujoco import sysid


def element(spec, kind, name):
    found = getattr(spec, kind)(name)
    if found is None:
        raise ValueError(f"the model has no {kind} named {name!r}")
    return found


def modifier(target):
    """``(spec, parameter) -> None`` that writes the parameter's value into the element the target names."""
    kind, name, field, index = target["kind"], target["name"], target["field"], target.get("index")
    if kind == "actuator":
        apply = sysid.apply_pgain if field == "kp" else sysid.apply_dgain
        return lambda spec, p: apply(spec, name, float(p.value[0]))

    def write(spec, p):
        found = element(spec, kind, name)
        if index is None:
            setattr(found, field, float(p.value[0]))
        else:
            values = np.array(getattr(found, field), dtype=float)
            values[index] = p.value[0]
            setattr(found, field, values)
    return write


def episode(path, model):
    """``(initial_state, control, sensordata)`` for one ``.npz`` file, checked against the model."""
    with np.load(path) as data:
        times, ctrl, sensors = (np.asarray(data[key], dtype=float) for key in ("time", "ctrl", "sensordata"))
        qpos = np.asarray(data["qpos"], dtype=float) if "qpos" in data else model.qpos0.copy()
        qvel = np.asarray(data["qvel"], dtype=float) if "qvel" in data else None
    if ctrl.shape[1] != model.nu or sensors.shape[1] != model.nsensordata:
        raise ValueError(f"{path.name}: ctrl has {ctrl.shape[1]} columns and sensordata {sensors.shape[1]}; "
                         f"the model has nu={model.nu} and nsensordata={model.nsensordata}")
    if qpos.shape != (model.nq,) or (qvel is not None and qvel.shape != (model.nv,)):
        raise ValueError(f"{path.name}: qpos must have {model.nq} values and qvel {model.nv}")
    return (sysid.create_initial_state(model, qpos, qvel), sysid.TimeSeries(times, ctrl),
            sysid.TimeSeries.from_names(times, sensors, model))


def sequences(spec, model, files, label):
    if not files:
        return None
    states, controls, sensors = zip(*(episode(path, model) for path in files))
    return sysid.ModelSequences(label, spec, [path.stem for path in files], list(states), list(controls), list(sensors))


def rms(values):
    values = np.concatenate([np.ravel(v) for v in values]) if values else np.zeros(0)
    return float(math.sqrt(np.mean(values ** 2))) if values.size else None


def errors(residual_fn, x, params, names):
    """Per-episode RMS of the normalised residual at parameter vector ``x``, and the RMS over all of them."""
    if residual_fn is None:
        return [], None
    residuals, _, _ = residual_fn(np.asarray(x, dtype=float), params.copy())
    return [{"episode": name, "rms": rms([r])} for name, r in zip(names, residuals)], rms(residuals)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("request")
    request_path = Path(parser.parse_args().request)
    started = time.monotonic()
    request = json.loads(request_path.read_text())
    dataset, out, holdout = Path(request["dataset"]), request_path.parent, set(request["holdout"])
    declared = request["parameters"]
    for relative, digest in request["files"].items():   # fit exactly what the host checked, unchanged since
        path = dataset / relative
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise SystemExit(f"{relative} changed after the fit was requested (or is a link); nothing was fitted.")
    spec = mujoco.MjSpec.from_file(str(dataset / "model.xml"))
    model = spec.compile()
    files = sorted(dataset / relative for relative in request["files"] if relative.startswith("episodes/"))
    fit_files = [f for f in files if f.stem not in holdout]
    held_files = [f for f in files if f.stem in holdout]
    fit_seq, held_seq = sequences(spec, model, fit_files, "fit"), sequences(spec, model, held_files, "holdout")

    params = sysid.ParameterDict()
    for item in declared:
        element(spec, item["target"]["kind"], item["target"]["name"])  # a missing element fails here, by name
        params.add(sysid.Parameter(item["name"], item["nominal"], item["min"], item["max"], modifier=modifier(item["target"])))
    fit_fn = sysid.build_residual_fn(models_sequences=[fit_seq])
    held_fn = sysid.build_residual_fn(models_sequences=[held_seq]) if held_seq else None
    x0 = params.as_vector()
    print("TWINSMITH_PROGRESS: 0.05 residual at the start", flush=True)
    fit_before, total_before = errors(fit_fn, x0, params, [f.stem for f in fit_files])
    held_before, held_total_before = errors(held_fn, x0, params, [f.stem for f in held_files])
    print("TWINSMITH_PROGRESS: 0.1 optimising", flush=True)
    fitted, result = sysid.optimize(initial_params=params, residual_fn=fit_fn, optimizer="mujoco", verbose=False,
                                    max_iters=request["max_iters"])
    x_hat = fitted.as_vector()
    fit_after, total_after = errors(fit_fn, x_hat, params, [f.stem for f in fit_files])
    held_after, held_total_after = errors(held_fn, x_hat, params, [f.stem for f in held_files])

    rows = []
    for item, value in zip(declared, x_hat):
        span = item["max"] - item["min"]
        near = 1e-6 * span
        at_bound = None if span <= 0 else ("min" if value <= item["min"] + near else "max" if value >= item["max"] - near else None)
        rows.append({"name": item["name"], "target": item["target"]["text"], "nominal": item["nominal"],
                     "fitted": float(value), "min": item["min"], "max": item["max"], "at_bound": at_bound})
    report = {
        "ok": True, "mujoco_version": mujoco.__version__, "optimizer": "mujoco", "max_iters": request["max_iters"],
        "parameters": rows, "bounds_hit": [row["name"] for row in rows if row["at_bound"]],
        "residual": {"before": total_before, "after": total_after,
                     "reduction": (1 - total_after / total_before) if total_before else None},
        "episodes": [dict(b, role="fit", rms_before=b.pop("rms"), rms_after=a["rms"]) for b, a in zip(fit_before, fit_after)]
                    + [dict(b, role="holdout", rms_before=b.pop("rms"), rms_after=a["rms"]) for b, a in zip(held_before, held_after)],
        "held_out": ({"episodes": [f.stem for f in held_files], "before": held_total_before, "after": held_total_after}
                     if held_files else None),
        "residual_note": "RMS of mujoco.sysid's normalised sensor residual (dimensionless), per episode and over all.",
        "objective_per_iteration": [float(v) for v in (result.get("extras") or {}).get("objective", [])],
        "wall_s": round(time.monotonic() - started, 2),
    }
    try:
        sysid.save_results(out / "upstream", [fit_seq], params, fitted, result, fit_fn)
        report["upstream"] = str(out / "upstream")
    except Exception as exc:  # the table above is the result; the upstream files are extra
        report["upstream_error"] = f"{type(exc).__name__}: {exc}"
    temporary = out / ".fit.json.tmp"
    temporary.write_text(json.dumps(report, indent=1) + "\n")
    temporary.replace(out / "fit.json")
    print(f"TWINSMITH_ARTIFACT: {out / 'fit.json'}", flush=True)
    print("TWINSMITH_PROGRESS: 1 done", flush=True)


if __name__ == "__main__":
    main()
