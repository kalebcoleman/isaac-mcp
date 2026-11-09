# Contributing

Issues and pull requests are welcome. Say which Isaac Sim build and physics backend you used.

- **Before opening a pull request:** run the host tests and doc checks: `cd mcp && uv run --locked --isolated --python 3.12 --extra test --extra isaaclab --extra scene-test pytest -q`, then
  `python3 scripts/check_docs.py` from the repository root. Don't run `uv run` or `uv sync` in the root: the root project installs Isaac Sim. Refresh the host lock with `uv lock --directory mcp`.
- **Changing a tool:** see [Changing tools](AGENTS.md#changing-tools). Every tool needs documentation, and the generated catalog and reference must match.
- **Live checks:** say what you ran on which build. A passing unit test doesn't show that a tool works in Kit.
- **Evidence:** use `<home>`-style placeholders, and don't commit robot assets. This repository is code-only: see [Assets](docs/ASSETS.md).
