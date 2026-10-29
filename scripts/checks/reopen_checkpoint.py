# Checks the reference checkpoint file against its manifest and opens it (async, timeline stopped).
# Checksums differ between machines after a re-export, so the checksum is reported, not required.
import asyncio, hashlib, json, pathlib
import omni.usd, omni.timeline
root = pathlib.Path('__ROOT__')
manifest = json.loads((root / 'checkpoints' / 'ur5e-dt-workstation-person-view-portable.json').read_text())
usda = root / 'checkpoints' / manifest['checkpoint']
if not usda.exists():
    result = {'checkpoint_present': False, 'file': usda.name, 'hint': 'run export_reference.py after quick_start'}
else:
    digest = hashlib.sha256(usda.read_bytes()).hexdigest()
    omni.timeline.get_timeline_interface().stop()
    asyncio.ensure_future(omni.usd.get_context().open_stage_async(str(usda)))
    result = {'checkpoint_present': True, 'file': usda.name, 'sha256_matches_manifest': digest == manifest['sha256'], 'opening': True}
