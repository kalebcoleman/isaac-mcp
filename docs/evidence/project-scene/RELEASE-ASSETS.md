# Draft release evidence assets

These are redacted records, not original private bytes. The draft release requires repository
access; use `gh release download untagged-8a38d708b61458020a38` while it remains unpublished.
The owner can review it with `gh release view untagged-8a38d708b61458020a38`. No assets are publicized
by marking this release draft.

| Asset URL | SHA-256 | Bytes |
|---|---|---:|
| [compatibility-followup-redacted.tar.gz](https://github.com/kalebcoleman/isaac-mcp/releases/download/untagged-8a38d708b61458020a38/compatibility-followup-redacted.tar.gz) | `d0e2020a8d360735f73652d4e54d9f706f02df9d4d45371ce0c4f8d41ff8fe88` | 4668085 |
| [historical-evidence-redacted.tar.gz](https://github.com/kalebcoleman/isaac-mcp/releases/download/untagged-8a38d708b61458020a38/historical-evidence-redacted.tar.gz) | `205b5d9bad4074d6f7d053d40231f07fa1dcb3dcdb154a2578926ea1d119d2bb` | 1089241 |
| [omitted-artifacts.json](https://github.com/kalebcoleman/isaac-mcp/releases/download/untagged-8a38d708b61458020a38/omitted-artifacts.json) | `e0227a3860e5641896e745da8f5f14ebdf2d892c9ce2f1ca9bde3be96b84ee2e` | 15357 |
| [sim-6001h-4499-records-redacted.tar.gz](https://github.com/kalebcoleman/isaac-mcp/releases/download/untagged-8a38d708b61458020a38/sim-6001h-4499-records-redacted.tar.gz) | `a094c434014450702e297088221e482368aa4929a94814a963c2a013c608dee7` | 1691622 |
| [sim-6001w-4499-records-redacted.tar.gz](https://github.com/kalebcoleman/isaac-mcp/releases/download/untagged-8a38d708b61458020a38/sim-6001w-4499-records-redacted.tar.gz) | `66017b346b8cdd9baab7fa73dc6c5b17777b923f00b9d2fb4c5d39c70f0217e2` | 2609839 |
| [viewport-6001w-eea18c8-redacted.tar.gz](https://github.com/kalebcoleman/isaac-mcp/releases/download/untagged-8a38d708b61458020a38/viewport-6001w-eea18c8-redacted.tar.gz) | `fd24bb6a09bd6f71feed6cbfb254c2d3bb0d5c9b35e7a85a4054ca4408a1858e` | 956920 |
| [agent-6001w-4499-redacted.tar.gz](https://github.com/kalebcoleman/isaac-mcp/releases/download/untagged-8a38d708b61458020a38/agent-6001w-4499-redacted.tar.gz) | `42202f3a1aae091cf627284c1b24162dda16bfb7f859ea11d5d01fd5548773fe` | 4600087 |
| [sim-601h-eea-urdf230-redacted.tar.gz](https://github.com/kalebcoleman/isaac-mcp/releases/download/untagged-8a38d708b61458020a38/sim-601h-eea-urdf230-redacted.tar.gz) | `0a8a62c9deee9f0af0232312ecb51fdd73c189ec56529d7bdb4decd16cdba089` | 3478861 |

`historical-evidence` preserves earlier failures and source comparison; `compatibility-followup`
contains the later release/build investigation, full suites, independent agents and cleanup.
`omitted-artifacts` records hashes of intentionally excluded binary/source files. Final-head
checks performed after the history rewrite are also attached to the PR/release with their
exact SHA and digest; the original run bundles remain immutable.

[Open the actual draft release](https://github.com/kalebcoleman/isaac-mcp/releases/tag/untagged-8a38d708b61458020a38).
Draft download URLs are GitHub-assigned; they can change if the owner publishes/retags it.

The four 6.0.0.1 supplements retain the exact `4499bf7` headless/windowed broad
records and independent windowed task, plus the `eea18c8` viewport qualification
and preceding `37a7358` failure. Their manifests hash distributed redacted bytes;
embedded source-observation hashes identify the original private records. Included
harnesses require explicit local installation and approved-asset configuration;
placeholder paths are not executable clean-install recipes. Asset geometry is
excluded. The agent bundle retains the original occluded video and the later clear
video, with a root review qualifying pre-fix camera/source-preservation claims.
These supplements do not certify the current candidate or the full version matrix.

The `sim-601h-eea-urdf230` supplement contains the next 6.0.1 headless broad run,
its later URDF missing-physics finding, and the fresh `230f4b3` narrow fix qualification.
The save harness exit 143 and expected-error parser failure remain separate from
successful assertions. No 6.0.1 windowed or other-build URDF pass is inferred.
[Compact source-identified checks](RECENT-CHECKS-20261102.json) map those observations.
