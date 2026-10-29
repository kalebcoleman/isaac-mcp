# Writes the reference checkpoint (name from its manifest) from the open stage, with relative asset paths.
# Run after quick_start. Replace __ROOT__ with the project path is handled by bridge_exec.
import json, pathlib
import omni.usd
root = pathlib.Path('__ROOT__')
manifest = json.loads((root / 'checkpoints' / 'ur5e-dt-workstation-person-view-portable.json').read_text())
target = root / 'checkpoints' / manifest['checkpoint']
stage = omni.usd.get_context().get_stage()
stage.GetRootLayer().Export(str(target))
text = target.read_text().replace('file://' + str(root) + '/', '../')
target.write_text(text)
result = {'written': target.name, 'relative_asset_refs': text.count('../external/'), 'absolute_refs_left': text.count(str(root))}
