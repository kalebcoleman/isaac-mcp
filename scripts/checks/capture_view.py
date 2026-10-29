# Saves the active viewport to resources/captures/ (git-ignored) with a UTC time stamp.
import datetime, os
import omni.kit.app, omni.kit.viewport.utility as vu
app = omni.kit.app.get_app()
for _ in range(120):
    app.update()
folder = '__ROOT__/resources/captures'
os.makedirs(folder, exist_ok=True)
path = folder + '/view-' + datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ') + '.png'
vu.capture_viewport_to_file(vu.get_active_viewport(), path)
for _ in range(30):
    app.update()
result = {'saved': path}
