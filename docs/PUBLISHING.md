# Publishing isaac-mcp (PyPI)

Publishing targets PyPI only. The removed npm launcher was a 24-line wrapper around `uvx`
and required uv to be installed anyway. Check PyPI name availability before publishing.

## Before publishing
1. `cd mcp && uv run --locked --isolated --python 3.12 --extra test --extra isaaclab --extra scene-test pytest -q`.
   Record the current count and commit.
   From the repo root also run `python3 scripts/check_docs.py` and review the latest CI result.
2. Decide the version in `mcp/pyproject.toml`.
3. Check `docs/VENDORING.md` and the LICENSE files: the UR5e twin contains Aldrin Inbaraj's MIT code. The bridge
   and client, first built on his unlicensed `omni-kit-mcp`, were rewritten here.
4. Verify the bundled-asset cleanup with `python3 scripts/check_assets.py`. The NVIDIA robot,
   sensor and material files, the Vention bench, the unused Kawasaki/RG2 scene and the Poliigon oak
   textures must not be in the public tree or its history. The Apache-2.0 and CC BY 4.0 notices are
   in `THIRD-PARTY-NOTICES.md`.
5. Fill in `project.urls` (repository, issues) in `mcp/pyproject.toml`; the repository is public.

## PyPI (Python host server)
```bash
cd mcp
rm -rf dist && uv build            # wheel + sdist in mcp/dist
uv publish --token <pypi-api-token>   # or: uv publish --trusted-publishing automatic from CI
uvx --from isaac-mcp isaac-mcp --version
```
Do a TestPyPI dry run first: `uv publish --publish-url https://test.pypi.org/legacy/ --token <testpypi-token>`.
Install check: `uv tool install isaac-mcp` then `isaac-mcp --version`. Optional Isaac Lab extra: `isaac-mcp[isaaclab]`.

## Client config after publishing
```json
{ "mcpServers": { "isaac": { "command": "uvx", "args": ["isaac-mcp"],
  "env": { "DISPLAY": ":0", "UR5E_DT_PORT": "8766" } } } }
```
(`DISPLAY` must match the desktop session; `UR5E_DT_PORT` is the Kit bridge port.)
