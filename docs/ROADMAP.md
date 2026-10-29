# Roadmap

1. **Verify on 6.2** — probe passes from a clean clone (done in the source repo; re-verify here).
2. **Separate the layers** — host server, generic Kit bridge, and tool packs each independently installable;
   `ur5e-dt` becomes one pack, not a requirement.
3. **Isaac Sim tools** — generic stage/prim/physics/camera/recording tools not tied to the UR5e.
4. **Isaac Lab 3.0 tools** — task discovery, env creation, train/eval launch, log/curve readout.
5. **Screen tools** — fix window targeting (Isaac window is not listed by `list_windows` today).
6. **Publish** — PyPI (`isaac-mcp` or a new name) and optionally npm.
