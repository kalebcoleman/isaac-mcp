# Export a private checkpoint copy; keep configured cache/source references valid.
# bridge_exec replaces __ROOT__ with this checkout, not the simulator install.
import json
import os
import pathlib
from urllib.parse import unquote, urlparse
import omni.usd
from pxr import Sdf, UsdUtils

root = pathlib.Path('__ROOT__')
manifest = json.loads((root / 'checkpoints' / 'ur5e-dt-workstation-person-view-portable.json').read_text())
target = root / 'checkpoints' / manifest['checkpoint']
source = omni.usd.get_context().get_stage().GetRootLayer()
output = Sdf.Layer.CreateAnonymous('.usda')
output.TransferContent(source)
relative_refs = []
absolute_refs = []

def make_relative(value):
    if not value:
        return value
    parsed = urlparse(value)
    if parsed.scheme == 'file' and parsed.netloc in ('', 'localhost'):
        value = unquote(parsed.path)
    elif parsed.scheme:
        return value  # Network/resolver identifiers are neither fetched nor rewritten.
    anchored = Sdf.ComputeAssetPathRelativeToLayer(source, value)
    if os.path.isabs(anchored):
        value = os.path.relpath(anchored, target.parent)
    if os.path.isabs(value):
        absolute_refs.append(value)
    elif value.startswith('../') or value.startswith('./'):
        relative_refs.append(value)
    return value

UsdUtils.ModifyAssetPaths(output, make_relative)
if not output.Export(str(target)):
    raise RuntimeError('Checkpoint export failed')
result = {'written': target.name, 'relative_asset_refs': len(relative_refs),
          'absolute_refs_left': len(absolute_refs),
          'scope': 'root-layer copy; local references remain relative to their configured cache/source; not a self-contained asset bundle'}
