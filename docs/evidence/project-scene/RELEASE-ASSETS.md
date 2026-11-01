# Draft release evidence assets

These are redacted records, not original private bytes. The draft release requires repository
access; use `gh release download project-scene-evidence` while it remains unpublished.
The owner can review it with `gh release view project-scene-evidence`. No assets are publicized
by marking this release draft.

| Asset URL | SHA-256 | Bytes |
|---|---|---:|
| [compatibility-followup-redacted.tar.gz](https://github.com/kalebcoleman/isaac-mcp/releases/download/project-scene-evidence/compatibility-followup-redacted.tar.gz) | `c9d54949e36ebc86057ce7b2520049b9f1933d6f5debac221e9de7c4e2e229d9` | 16485989 |
| [historical-evidence-redacted.tar.gz](https://github.com/kalebcoleman/isaac-mcp/releases/download/project-scene-evidence/historical-evidence-redacted.tar.gz) | `1651271544878f130df4f069800e3d97ad469302b51479860da4c39abf15ef18` | 7392202 |
| [omitted-artifacts.json](https://github.com/kalebcoleman/isaac-mcp/releases/download/project-scene-evidence/omitted-artifacts.json) | `e0227a3860e5641896e745da8f5f14ebdf2d892c9ce2f1ca9bde3be96b84ee2e` | 15357 |

`historical-evidence` preserves earlier failures and source comparison; `compatibility-followup`
contains the later release/build investigation, full suites, independent agents and cleanup.
`omitted-artifacts` records hashes of intentionally excluded binary/source files. Final-head
checks performed after the history rewrite are also attached to the PR/release with their
exact SHA and digest; the original run bundles remain immutable.
