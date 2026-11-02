# Root/reference checkpoint plus its composed local PhysicsScene; never overwrites.
import datetime
import pathlib
import runpy
import omni.usd

VARIANT = 'person-view'
STAMP = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
root = pathlib.Path('__ROOT__')
target = root / 'checkpoints' / ('ur5e-dt-workstation-' + VARIANT + '-' + STAMP + '.usda')
export = runpy.run_path(str(root / 'scripts/checkpoint_export.py'))['export_checkpoint']
result = export(omni.usd.get_context().get_stage(), target)
result.update(exported=True, file=str(target))
