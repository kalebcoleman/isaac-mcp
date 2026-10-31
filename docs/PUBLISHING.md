# Publishing isaac-mcp (PyPI and npm)

Not published yet. Names `isaac-mcp` on PyPI and npm were both unclaimed on 2026-10-29: re-check first
(`curl -s -o /dev/null -w "%{http_code}\n" https://pypi.org/pypi/isaac-mcp/json` and
`https://registry.npmjs.org/isaac-mcp`; 404 = free).

## Before publishing
1. `cd mcp && uv run --isolated --python 3.12 --with-editable . --extra test --extra isaaclab pytest -q`.
   Record the current count and commit; see [ROADMAP.md](ROADMAP.md) for the last dated baseline.
2. Decide the version in `mcp/pyproject.toml` and `npm/package.json` (keep them equal).
3. Check `docs/VENDORING.md` and the LICENSE files: the package contains Aldrin Inbaraj's MIT code. The upstream
   `omni-kit-mcp` has no LICENSE file; consider asking `inbarajaldrin` to confirm before a public release.
4. Fill in `project.urls` (repository, issues) in `mcp/pyproject.toml` once the repo is public.
5. Make the GitHub repo public (it is private now) if the PyPI/npm pages should link to it.

## PyPI (Python host server)
```bash
cd mcp
rm -rf dist && uv build            # wheel + sdist in mcp/dist
uv publish --token <pypi-api-token>   # or: uv publish --trusted-publishing automatic from CI
uvx --from isaac-mcp isaac-mcp --version
```
Do a TestPyPI dry run first: `uv publish --publish-url https://test.pypi.org/legacy/ --token <testpypi-token>`.
Install check: `uv tool install isaac-mcp` then `isaac-mcp --version`. Optional Isaac Lab extra: `isaac-mcp[isaaclab]`.

## npm (npx launcher)
`npm/` is only a launcher: it runs the Python server through `uvx`. Publish it after PyPI, then set
`DEFAULT_FROM` in `npm/bin/isaac-mcp.js` to `isaac-mcp` (the PyPI name) instead of the GitHub URL.
```bash
cd npm
npm pack --dry-run                 # check the file list (bin, README, LICENSE)
npm login && npm publish
npx -y isaac-mcp --version
```

## Client config after publishing
```json
{ "mcpServers": { "isaac": { "command": "uvx", "args": ["isaac-mcp"],
  "env": { "DISPLAY": ":1", "UR5E_DT_PORT": "8766" } } } }
```
(`DISPLAY` must match the desktop session; `UR5E_DT_PORT` is the Kit bridge port.)
