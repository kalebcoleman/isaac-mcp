"""Place a converted URDF or MuJoCo robot on the stage."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid
from isaac_mcp.project_assets import AssetError, refuse


class RobotImport:
    """Mixed into Advanced: uses its scene (``self.s``), sensors and ``physics_manager``."""

    def import_urdf(self, stage, snapshot_id, manifest_sha256, prim_path, fixed_base=True, default_drives=True, joint_targets=None, **pose):
        from isaac_mcp import urdf_physics, urdf_refs
        from isaac_mcp.project_assets import Broker, open_dir, signature

        from .validate import apply_joint_targets, joint_summary, validate
        from .urdf import (
            convert, importer_identity, place_above_ground, prepare_generated_physics,
            repair_meshes_for_legacy_importer, uses_legacy_importer,
        )

        s = self.s
        if type(fixed_base) is not bool or type(default_drives) is not bool:
            refuse("INVALID_VALUE", "fixed_base and default_drives must be booleans.")
        s.new_path(stage, prim_path)
        filename, manifest = s.broker.verify(snapshot_id, manifest_sha256, kind="urdf")
        urdf_refs.check_urdf_path(filename.name)
        entry = manifest["entry"]
        tree = urdf_refs.parse(filename.read_bytes(), entry)
        project_dir = filename.parents[len(Path(entry).parts) - 1]
        project = urdf_refs.project_from_manifest(project_dir, manifest["files"], manifest.get("root_name", ""))
        found = urdf_refs.resolve(entry, tree, project)
        if found["problems"]:
            urdf_refs.refusal_for(found["problems"], entry)
        followed = urdf_refs.follow_mesh_files(project, sorted(set(found["mapped"].values())))
        model = urdf_physics.read_model(tree)
        workspace = s.broker.staging / snapshot_id
        # The rewritten URDF stays in private staging, and only there: not the source and not the
        # pack, and outside the verified project folder. Each file it names is in that folder.
        rewritten = workspace / "urdf-input" / filename.name
        rewritten.parent.mkdir(exist_ok=True)
        mapped, repaired = found["mapped"], []
        if uses_legacy_importer():
            mapped, repaired = repair_meshes_for_legacy_importer(mapped, project, rewritten.parent / "repaired")
        data = urdf_refs.rewritten(tree, mapped, project_dir)
        if not rewritten.is_file() or rewritten.read_bytes() != data:
            rewritten.write_bytes(data)
        key = hashlib.sha256(json.dumps([importer_identity(), fixed_base, default_drives]).encode() + data).hexdigest()[:24]
        output = workspace / f"converted-{key}"
        root_before = stage.GetRootLayer().ExportToString()
        session_before = stage.GetSessionLayer().ExportToString()
        done = output / "generated.txt"
        if done.is_file():
            generated = output / done.read_text()
        else:
            pending = workspace / f".pending-{uuid.uuid4().hex}"
            try:
                try:
                    generated = convert(rewritten, pending, fixed_base)
                except AssetError:
                    raise
                except Exception as exc:  # noqa: BLE001 - the native importer raises anything
                    refuse("IMPORT_FAILED", f"The URDF importer failed on {entry}: {str(exc)[:800]}")
                if (
                    s.get_stage() != stage
                    or stage.GetRootLayer().ExportToString() != root_before
                    or stage.GetSessionLayer().ExportToString() != session_before
                ):
                    refuse(
                        "IMPORTER_STAGE_CHANGED",
                        "Native importer changed the active stage. Partial effects possible; inspect before retrying.",
                    )
                converted_physics = prepare_generated_physics(
                    generated,
                    pending,
                    movable_joints=sum(
                        joint.get("type") in ("revolute", "continuous", "prismatic")
                        for joint in tree.findall("joint")
                    ),
                    gains=urdf_physics.default_gains(model) if default_drives else None,
                    fixed_base=fixed_base,
                )
                relative_generated = str(generated.relative_to(pending))
                (pending / "generated.txt").write_text(relative_generated)
                (pending / "report.json").write_text(json.dumps(converted_physics))
                pending.rename(output)
                generated = output / relative_generated
            except BaseException:
                shutil.rmtree(pending, ignore_errors=True)
                raise
        converted_physics = json.loads((output / "report.json").read_text())
        # Generated artifacts are service-owned derivatives, not an approval of a
        # new model-supplied folder. Reuse the normal bounded snapshot pipeline.
        derived = Broker("")
        derived.staging = s.broker.staging
        fd = open_dir(output)
        try:
            ident = signature(os.fstat(fd))[:2]
        finally:
            os.close(fd)
        derived.roots = {"converted": (output, ident)}
        snap = derived.snapshot("converted", str(generated.relative_to(output)))
        position_given = "position" in pose
        result = s.import_asset(
            stage, snap["snapshot_id"], snap["manifest_sha256"], prim_path, **pose
        )
        from .ros2 import _articulation_roots

        roots = _articulation_roots(stage.GetPrimAtPath(prim_path))
        warnings = urdf_physics.warnings(model)
        placement = place_above_ground(stage, prim_path, position_given)
        if "warning" in placement:
            warnings.append(placement.pop("warning"))
        if followed["missing"]:
            warnings.append(f"Meshes name files that are absent, so those textures will be missing: "
                            f"{[item['reference'] for item in followed['missing'][:5]]}.")
        if converted_physics["base"] != ("fixed" if fixed_base else "floating"):
            warnings.append(f"The converted robot's base is {converted_physics['base']}, not what fixed_base asked for.")
        if fixed_base and any(joint["type"] == "continuous" for joint in model["joints"]):
            warnings.append("This robot has continuous joints, which may be wheels, and its base is fixed to the world. "
                            "For a robot that drives, pass fixed_base=false.")
        targets_report = apply_joint_targets(stage, prim_path, joint_targets) if joint_targets else None
        if targets_report and targets_report["unknown"]:
            warnings.append(f"joint_targets names joints that do not exist: {targets_report['unknown'][:6]}. Valid names: {targets_report['valid'][:12]}.")
        if targets_report and targets_report["beyond_limits"]:
            warnings.append(f"joint_targets beyond a joint's limits were set to the limit: {targets_report['beyond_limits']}.")
        result.update(
            joints=joint_summary(stage, prim_path),
            **({"joint_targets_applied": targets_report["applied"]} if targets_report else {}),
            urdf_snapshot_id=snapshot_id,
            converted_physics=converted_physics,
            articulation_root=roots[0] if roots else None,
            articulation_roots=roots,
            base=converted_physics["base"],
            packages=found["packages"],
            mapped_files=len(found["mapped"]),
            **({"repaired_meshes": repaired} if repaired else {}),
            placement=placement,
            warnings=warnings,
            validation=validate(stage, prim_path, expect_robot=True),
        )
        return result

    def import_mjcf(self, stage, snapshot_id, manifest_sha256, prim_path, fixed_base=None, default_drives=False, joint_targets=None, **pose):
        """Import a MuJoCo MJCF model from a verified snapshot, the way ``import_urdf`` imports a URDF.

        The host checked that every file the model names is inside the approved folder before it made the snapshot, and
        this method checks again against the snapshot's own file list, so the importer reads nothing else.
        """
        from isaac_mcp import mjcf_refs, urdf_refs
        from isaac_mcp.project_assets import Broker, open_dir, signature

        from .validate import apply_joint_targets, joint_summary, validate
        from .mjcf import (convert, importer_identity, mjcf_default_gains, movable_joint_count,
                           restore_legacy_equalities, tendon_warning, uses_legacy_importer)
        from .urdf import MAX_AUTO_LIFT_M, place_above_ground, prepare_generated_physics, undriven_joints

        s = self.s
        if fixed_base is not None and type(fixed_base) is not bool:
            refuse("INVALID_VALUE", "fixed_base must be true, false, or left out to keep what the model says.")
        if type(default_drives) is not bool:
            refuse("INVALID_VALUE", "default_drives must be a boolean.")
        s.new_path(stage, prim_path)
        filename, manifest = s.broker.verify(snapshot_id, manifest_sha256, kind="mjcf")
        mjcf_refs.check_mjcf_path(filename.name)
        entry = manifest["entry"]
        project_dir = filename.parents[len(Path(entry).parts) - 1]
        project = urdf_refs.project_from_manifest(project_dir, manifest["files"], manifest.get("root_name", ""))
        found = mjcf_refs.resolve(entry, project)
        if found["problems"]:
            mjcf_refs.refusal_for(found["problems"], entry)
        movable = movable_joint_count(found["trees"].values())
        workspace = s.broker.staging / snapshot_id
        key = hashlib.sha256(json.dumps([importer_identity(), fixed_base, default_drives, manifest_sha256]).encode()).hexdigest()[:24]
        output = workspace / f"mjcf-converted-{key}"
        root_before = stage.GetRootLayer().ExportToString()
        session_before = stage.GetSessionLayer().ExportToString()
        done = output / "generated.txt"
        if done.is_file():
            generated = output / done.read_text()
        else:
            pending = workspace / f".pending-{uuid.uuid4().hex}"
            try:
                try:
                    generated = convert(filename, pending, fixed_base, project_dir, found)
                except AssetError:
                    raise
                except Exception as exc:  # noqa: BLE001 - the native importer raises anything
                    refuse("IMPORT_FAILED", f"The MJCF importer failed on {entry}: {str(exc)[:800]}")
                if (
                    s.get_stage() != stage
                    or stage.GetRootLayer().ExportToString() != root_before
                    or stage.GetSessionLayer().ExportToString() != session_before
                ):
                    refuse(
                        "IMPORTER_STAGE_CHANGED",
                        "Native importer changed the active stage. Partial effects possible; inspect before retrying.",
                    )
                converted_physics = prepare_generated_physics(
                    generated, pending, movable_joints=movable, gains=mjcf_default_gains(found["trees"].values()) if default_drives else None,
                    fixed_base=fixed_base, kind="MJCF",
                )
                if uses_legacy_importer():
                    restored = restore_legacy_equalities(generated, found["trees"].values())
                    declared = sum(1 for tree in found["trees"].values() for joint in tree.findall("./equality/joint")
                                   if joint.get("active", "true").lower() not in ("false", "0"))
                    if restored:
                        converted_physics["restored_joint_equalities"] = restored
                    if declared > len(restored):
                        converted_physics["unrestored_joint_equalities"] = declared - len(restored)
                relative_generated = str(generated.relative_to(pending))
                (pending / "generated.txt").write_text(relative_generated)
                (pending / "report.json").write_text(json.dumps(converted_physics))
                pending.rename(output)
                generated = output / relative_generated
            except BaseException:
                shutil.rmtree(pending, ignore_errors=True)
                raise
        converted_physics = json.loads((output / "report.json").read_text())
        derived = Broker("")
        derived.staging = s.broker.staging
        fd = open_dir(output)
        try:
            ident = signature(os.fstat(fd))[:2]
        finally:
            os.close(fd)
        derived.roots = {"converted": (output, ident)}
        snap = derived.snapshot("converted", str(generated.relative_to(output)))
        position_given = "position" in pose
        result = s.import_asset(stage, snap["snapshot_id"], snap["manifest_sha256"], prim_path, **pose)
        from .ros2 import _articulation_roots

        roots = _articulation_roots(stage.GetPrimAtPath(prim_path))
        warnings = []
        tendon_note = tendon_warning(found["trees"].values())
        if tendon_note:
            warnings.append(tendon_note)
        if converted_physics.get("restored_joint_equalities"):
            warnings.append("Sim 5.1 approximated simple MJCF prismatic joint equalities as PhysX mimic constraints; MuJoCo solver tuning may differ.")
        if converted_physics.get("unrestored_joint_equalities"):
            warnings.append(f"Sim 5.1's adapter did not restore {converted_physics['unrestored_joint_equalities']} MJCF joint equalities; inspect the converted constraints before relying on them.")
        placement = place_above_ground(stage, prim_path, position_given, max_lift=MAX_AUTO_LIFT_M)
        if "warning" in placement:
            warnings.append(placement.pop("warning"))
        if fixed_base is not None and converted_physics["base"] != ("fixed" if fixed_base else "floating"):
            warnings.append(f"The converted robot's base is {converted_physics['base']}, not what fixed_base asked for.")
        if not roots:
            warnings.append("No articulation root was found, so the joint tools will not work on this model.")
        undriven = undriven_joints(stage, prim_path)
        if undriven and not default_drives:
            warnings.append(f"{len(undriven)} movable joints have no drive (the model's actuators are torque motors, so it will "
                            "collapse under gravity): hold it with a controller, set gains with sim__joints__gains, "
                            "or import again with default_drives=true for a generic holding drive.")
        targets_report = apply_joint_targets(stage, prim_path, joint_targets) if joint_targets else None
        if targets_report and targets_report["unknown"]:
            warnings.append(f"joint_targets names joints that do not exist: {targets_report['unknown'][:6]}. Valid names: {targets_report['valid'][:12]}.")
        if targets_report and targets_report["beyond_limits"]:
            warnings.append(f"joint_targets beyond a joint's limits were set to the limit: {targets_report['beyond_limits']}.")
        result.update(
            joints=joint_summary(stage, prim_path),
            **({"joint_targets_applied": targets_report["applied"]} if targets_report else {}),
            undriven_joints=undriven,
            mjcf_snapshot_id=snapshot_id,
            converted_physics=converted_physics,
            articulation_root=roots[0] if roots else None,
            articulation_roots=roots,
            base=converted_physics["base"],
            mapped_files=len(found["mapped"]),
            includes=found["includes"],
            placement=placement,
            warnings=warnings,
            validation=validate(stage, prim_path, expect_robot=True),
        )
        return result
