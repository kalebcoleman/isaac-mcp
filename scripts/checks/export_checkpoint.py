# Saves the open stage as a new checkpoint with a fresh UTC time stamp (never overwrites).
# Variant names the scene, e.g. "person-view". Set VARIANT before sending.
import datetime
import omni.usd
VARIANT = 'person-view'
STAMP = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
DST = '__ROOT__/checkpoints/ur5e-dt-workstation-' + VARIANT + '-' + STAMP + '.usda'
stage = omni.usd.get_context().get_stage()
ok = stage.GetRootLayer().Export(DST)
result = {'exported': bool(ok), 'file': DST}
