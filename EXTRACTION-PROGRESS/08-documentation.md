# Phase 8 — Documentation

The last phase: make both repos explain themselves to someone who wasn't in
this session, without having to read `EXTRACTION-PROGRESS/` or the plan
itself.

## fordge-ledger

`README.md`, section "The in-browser SCAD editor" (previously ~60 lines of
WASM/Manifold/grammar internals) rewritten to ~30 lines describing the
*split* instead: the editor is a separate image
([forge-scad-editor](https://github.com/Huntler/forge-scad-editor)), not part
of this repo or this image; enable/disable it by adding or removing its
compose service (no rebuild); its tab's visibility is driven by a runtime
probe of `FORGE_EDITOR_URL`, not a build-time flag. Ends by pointing at the
other repo's README for how the editor itself actually works.

One sentence added to "Deploying to the NAS": the same `docker compose pull`
also pulls the editor image, with a link back to the section above for
anyone who'd rather not run it.

Checked (`grep`) that no other README section still references the editor's
removed tech stack (CodeMirror, three.js, etc.) — the only remaining hits are
harmless example file listings, not descriptions of how it's built.

Committed as `66ada0f`.

## forge-scad-editor

Two new files, both absent until this phase:

**`README.md`** — written from scratch, covering:

- Running it standalone (`docker compose up` / `container-compose up`, or
  `test-editor-standalone.sh` for an instant scratch library) and embedding
  it in Forge Ledger (the compose snippet, copied from the host's actual
  service block so it can't drift from what's really there).
- The `SCADED_*` config table (`host_mode`, `host_api`, `library_path`,
  `cors_origins`), sourced directly from `config.py`'s own comments.
- `/editor/api/health`'s exact response shape and what each field means for
  the host's availability probe — `host_contract` (must match the host's
  `HOST_CONTRACT_VERSION`) and `library_marker` (the per-instance UUID that
  catches "right mount path, wrong instance" that a path comparison alone
  would miss).
- **The host API contract**, reproduced from the plan's §1.4 table with the
  one addition it grew since (`base_hash` on the write endpoint, R10) —
  explicitly called out as a *frozen surface*: change it in both repos
  together, bump `host_contract` in both, or a mismatched pair should fail
  the probe rather than fail confusingly at some specific request.
- Why this repo's own API lives under `/editor/api/*` rather than bare
  `/api/*` — the one deliberate deviation from the plan's literal §2.2
  diagram, and the reason the host's proxy can be a single unprefixed
  forward with no path rewriting.
- The D3 design-token duplication: `tokens.css` / `tailwind.tokens.cjs` are
  kept byte-for-byte identical by hand with their fordge-ledger counterparts,
  no shared package between the repos; each file's header names the other
  side.
- The WASM/Manifold/grammar/tool-snippet internals prose, moved here
  **verbatim** from fordge-ledger's old README section (the WASM-fetch
  script and its pinning, the Manifold backend and its ~40x measured
  speedup, the "fresh module instance per render" workaround for the
  known WASM reuse bug, the hand-written Lezer grammar driving lint/
  autocomplete without a WASM round-trip per keystroke, and how tool
  snippets get copied into `models/sources/tools/` and cleaned up).
- Testing (`pytest`, `vitest`, `typecheck`) and a short note on the two
  cross-repo shared-fixture tests (`content_hash_cases.json`,
  `tool_use_cases.json`) that keep the FNV-1a hash and `TOOL_USE_RE`'s four
  independent implementations from silently drifting apart.
- What the CI workflow (below) actually publishes and when.

**`.github/workflows/docker-publish.yml`** — adapted from fordge-ledger's own
workflow. No structural changes were needed: `IMAGE_NAME: ${{ github.repository }}`
already resolves per-repo, so the same amd64-on-main / multi-arch-plus-semver-
on-tags shape just works for `ghcr.io/huntler/forge-scad-editor` unchanged.

Also cleaned up a stray `.test-editor-standalone.library` file — a state file
`test-editor-standalone.sh` leaves behind while its scratch stack is up,
pointing at an already-deleted temp directory — and added it to `.gitignore`
so it doesn't get committed by accident on a future run.

Both README's cross-link each other: fordge-ledger's editor section links to
`github.com/Huntler/forge-scad-editor`; forge-scad-editor's README links back
to `github.com/Huntler/fordge-ledger` in its opening paragraph and again next
to the embedding instructions.

Committed as `4498639`.

## Summary across all 8 phases

The extraction plan is fully implemented:

- **forge-scad-editor** exists as an independent repo with preserved git
  history (`git filter-repo`), builds as its own Docker image, and runs
  standalone over any folder (`local` mode) or embedded in Forge Ledger
  (`forge` mode) via a small REST contract and a `HostAdapter` seam.
- **fordge-ledger** no longer contains any editor code — deleted outright,
  not just unused — and instead proxies `/editor/*` to the editor container
  and polls its health for an availability-driven tab.
- Two real defects were found and fixed by actually running both containers
  together and driving the result through Chrome, not just by reviewing the
  code: R5 (missing `Cache-Control` on wasm/assets) and R12 (a path-traversal
  rejection reaching the frontend as a raw 500 instead of a clean 400).
- One piece of plan-mandated behavior (R10, the stale-write guard) hadn't
  actually been built yet going into Phase 7's verification pass and was
  implemented then, using FNV-1a instead of the plan's suggested SHA-256
  once the secure-context requirement of `crypto.subtle` made SHA-256 a
  dead end for this app's plain-HTTP LAN deployment.
- Both repos now document themselves independently: a newcomer to either one
  can understand what it does, how to run it, and how the two halves talk to
  each other without reading `EXTRACTION-PROGRESS/` or the original plan.

See [00](00-baseline.md) through [07](07-verification.md) for the full
per-phase record.
