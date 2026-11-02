# Root/reference checkpoint plus its composed local PhysicsScene; never overwrites.
import json
import pathlib
import runpy
import omni.usd

root = pathlib.Path('__ROOT__')
manifest = json.loads((root / 'checkpoints' / 'ur5e-dt-workstation-person-view-portable.json').read_text())
export = runpy.run_path(str(root / 'scripts/checkpoint_export.py'))['export_checkpoint']
result = export(omni.usd.get_context().get_stage(), root / 'checkpoints' / manifest['checkpoint'])
