# Draft release evidence assets

These are redacted records, not original private bytes. The draft release requires repository
access; use `gh release download project-scene-evidence` while it remains unpublished.
The owner can review it with `gh release view project-scene-evidence`. No assets are publicized
by marking this release draft.

| Asset URL | SHA-256 | Bytes |
|---|---|---:|
| [compatibility-followup-redacted.tar.gz](https://github.com/kalebcoleman/isaac-mcp/releases/download/untagged-8a38d708b61458020a38/compatibility-followup-redacted.tar.gz) | `d0e2020a8d360735f73652d4e54d9f706f02df9d4d45371ce0c4f8d41ff8fe88` | 4668085 |
| [historical-evidence-redacted.tar.gz](https://github.com/kalebcoleman/isaac-mcp/releases/download/untagged-8a38d708b61458020a38/historical-evidence-redacted.tar.gz) | `205b5d9bad4074d6f7d053d40231f07fa1dcb3dcdb154a2578926ea1d119d2bb` | 1089241 |
| [omitted-artifacts.json](https://github.com/kalebcoleman/isaac-mcp/releases/download/untagged-8a38d708b61458020a38/omitted-artifacts.json) | `e0227a3860e5641896e745da8f5f14ebdf2d892c9ce2f1ca9bde3be96b84ee2e` | 15357 |

`historical-evidence` preserves earlier failures and source comparison; `compatibility-followup`
contains the later release/build investigation, full suites, independent agents and cleanup.
`omitted-artifacts` records hashes of intentionally excluded binary/source files. Final-head
checks performed after the history rewrite are also attached to the PR/release with their
exact SHA and digest; the original run bundles remain immutable.

[Open the actual draft release](https://github.com/kalebcoleman/isaac-mcp/releases/tag/untagged-8a38d708b61458020a38).
Draft download URLs are GitHub-assigned; they can change if the owner publishes/retags it.
