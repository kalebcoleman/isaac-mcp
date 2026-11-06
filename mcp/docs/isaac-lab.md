# Isaac Lab


The bridge's only Kit dependency (`omni.kit.async_engine`) ships in Kit's core
extension set, so it runs in any Kit app — including Isaac Lab. Lab
*standalone scripts* (train/play) differ from the Isaac Sim app in three ways:
the script owns the Kit app (`AppLauncher` + Lab's own `isaaclab.python.*.kit`
experiences), so persistent registration doesn't apply — enable the bridge
per-script via `--kit_args`; the script owns the main loop, so bridge commands
execute only while Kit updates, and a script blocked between updates stalls dispatch; and
configuration is env-var only. In the Lab 3.0.0 source, `sim.step()` and `env.step()` update Kit
only through a windowed Kit visualizer (`KitVisualizer.step` returns early when headless), so call
`simulation_app.update()` in your loop, with `/app/player/playSimulations` off around it as Lab does
so that Kit does not also advance physics. The template shows this. Headless without it, the port listens
but `status` never answers (seen with `sim.step()`; `env.step()` was read from source, not run). Note the port: "one bridge, one port, *per Kit process*" —
a Lab standalone run is its own Kit process, so when it runs alongside the
installed app (which holds 8766) it needs its own port (8767 by convention).
See `mcp/templates/isaaclab_bridge_launch.py` for the template.
