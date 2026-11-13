# Publishing twinsmith (PyPI)

Publishing targets PyPI only. The removed npm launcher was a 24-line wrapper around `uvx`
and required uv to be installed anyway. Check PyPI name availability before publishing.

## Before publishing
1. `cd mcp && uv run --locked --isolated --python 3.12 --extra test --extra isaaclab --extra scene-test pytest -q`.
   Record the current count and commit.
   From the repo root also run `python3 scripts/check_docs.py` and review the latest CI result.
2. Decide the version in `mcp/pyproject.toml`.
3. Check `docs/VENDORING.md` and the LICENSE files: the UR5e work cell contains Aldrin Inbaraj's MIT code. The bridge
   and client, first built on his unlicensed `omni-kit-mcp`, were rewritten here.
4. Verify the bundled-asset cleanup with `python3 scripts/check_assets.py`. The NVIDIA robot,
   sensor and material files, the Vention bench, the unused Kawasaki/RG2 scene and the Poliigon oak
   textures must not be in the public tree or its history. The Apache-2.0 and CC BY 4.0 notices are
   in `THIRD-PARTY-NOTICES.md`.
5. Fill in `project.urls` (repository, issues) in `mcp/pyproject.toml`; the repository is public.
6. **A release states the engine and Isaac Sim build it was qualified on.** Put both in the release notes, with the Isaac Lab
   version if Lab was exercised, taken from a recorded live run (see [verification](VERIFICATION.md)) and not from memory. The
   target engine and its current limits are in [compatibility](COMPATIBILITY.md#target-engine); do not state a stronger claim
   than the last recorded run shows. A release whose live checks did not run says that.

## PyPI (Python host server)
```bash
cd mcp
rm -rf dist && uv build            # wheel + sdist in mcp/dist
uv publish --token <pypi-api-token>   # or: uv publish --trusted-publishing automatic from CI
uvx --from twinsmith twinsmith --version
```
Do a TestPyPI dry run first: `uv publish --publish-url https://test.pypi.org/legacy/ --token <testpypi-token>`.
Install check: `uv tool install twinsmith` then `twinsmith --version`. Optional Isaac Lab extra: `twinsmith[isaaclab]`.

## Client config after publishing
```json
{ "mcpServers": { "twinsmith": { "command": "uvx", "args": ["twinsmith"],
  "env": { "DISPLAY": ":0", "TWINSMITH_PORT": "8766" } } } }
```
(`DISPLAY` must match the desktop session; `TWINSMITH_PORT` is the Kit bridge port. Add `TWINSMITH_TOOLSETS` to change which
[toolsets](TOOLSETS.md) the client is shown.)
