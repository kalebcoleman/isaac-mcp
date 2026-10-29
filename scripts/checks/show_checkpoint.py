# Opens the portable workstation checkpoint in the running app and saves a view of it.
# The viewport switches to /World/person_view when the file has it (see the extension's stage handler).
import asyncio, datetime, os
import omni.usd, omni.kit.app, omni.timeline, omni.kit.viewport.utility as vu

USDA = '__ROOT__/checkpoints/ur5e-dt-workstation-person-view-portable-20261029T004117Z.usda'

async def _show():
    omni.timeline.get_timeline_interface().stop()
    await omni.usd.get_context().open_stage_async(USDA)
    app = omni.kit.app.get_app()
    for _ in range(120):
        await app.next_update_async()
    folder = '__ROOT__/resources/captures'
    os.makedirs(folder, exist_ok=True)
    path = folder + '/checkpoint-' + datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ') + '.png'
    vu.capture_viewport_to_file(vu.get_active_viewport(), path)

asyncio.ensure_future(_show())
result = {'opening': USDA.split('/')[-1]}
