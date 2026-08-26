# Phase 3 — Host-side proxy and availability probe

Repo: this one (`fordge-ledger/backend`).

## A design gap found and fixed first (affects both repos)

Before writing the proxy, re-reading the plan's §2.2 diagram against what
Phase 2 had actually built surfaced a real problem: the diagram sketches the
editor container's own API (`/api/tools`, `/api/health`, …) at a **bare**
`/api/...`, separate from its SPA at `/editor/...`. But the editor's SPA
fetches its own backend using root-relative paths — when that SPA is loaded
*through* the host's `/editor/*` proxy, those `fetch("/api/tools")` calls
would hit `https://forge-ledger/api/tools`, which is the **host's own**
`/api/*` namespace (projects/prints/etc — `tools` was removed from it in
Phase 5), not the editor's. Bare `/api/*` on the editor container only works
when nothing is proxying it (standalone `local` mode, hit directly).

Fixed on the `forge-scad-editor` side (see that repo's own progress notes,
commit "Move this repo's own API under /editor/ too"): the editor's own API
now lives under `/editor/api/*`, matching its SPA. That makes the host's
proxy rule trivial and uniform — forward `/editor/*` verbatim, no path
rewriting, and the exact same built SPA works identically whether hit
directly or through this proxy. Re-verified standalone (container-compose +
a live Chrome pass) after the change before starting this phase.

## 3a — `config.py`

Added `editor_url: str = ""` (env `FORGE_EDITOR_URL`).

## 3b — `api/editor.py`

Streaming reverse proxy, `httpx.AsyncClient`, mounted at `/editor/{path:path}`
for `GET/POST/PUT/DELETE/PATCH`. Streams both directions (`request.stream()`
in, `aiter_bytes()` out — never buffers, matters for the ~9.6 MB wasm, R5).
Strips only genuinely hop-by-hop headers (`Connection`, `Transfer-Encoding`,
etc.) and passes everything else — `Content-Type`, `Content-Length`, `ETag`,
`Cache-Control` — straight through, which is what lets the browser cache the
wasm across visits. Returns a clear `503` with a body explaining why
(`"editor is not configured"` or, from an unreachable upstream, `502` with
the underlying error) rather than a bare connection error.

Mounted in `main.py` **before** the SPA catch-all — same ordering rule the
MCP mount already follows, called out explicitly in both places.

## 3c — Availability probe (`AppState.editor_status`)

- `HOST_CONTRACT_VERSION = 1` on `AppState`, mirrored on the editor side as
  the same constant in its `api/system.py` (currently also `1` — the two
  must be bumped together on any host-contract change).
- `library_marker(create: bool = False)`: reads (or, at startup only,
  creates) `<library>/_shared/.forge-instance` — a bare UUID, create-if-
  missing, never overwritten. `startup()` now calls
  `self.library_marker(create=True)` once.
- `editor_status()`: 60s-TTL, 2s-timeout cached probe of
  `{FORGE_EDITOR_URL}/editor/api/health` (note the `/editor` prefix — see
  the design-gap fix above; this hits the same path the browser's proxied
  requests would, even though this particular call is server-to-server and
  technically didn't have to). Three failure modes, each with a distinct
  `reason` string: not configured, unreachable (network/timeout/non-2xx,
  caught broadly on purpose — any failure here means "hide the tab," never
  a 500 on the host's own `/api/health`), contract mismatch, library
  mismatch. Logs only on a `available` state *transition*, not every poll.
- `api/system.py`'s `health()` now includes
  `"editor": state.editor_status()` in its response.

## Verified

- Full backend suite: **177 passed** (one `test_empty_models_folder_cannot_be_snapshotted`
  failure on the first run turned out to be pre-existing flakiness —
  reproduces in isolation as a pass, and the full suite is consistently
  green on repeated runs; unrelated to anything touched in this phase, since
  none of Phase 3's changes touch `services/library.py`'s snapshot path).
- `ruff check` clean on every file this phase touched (two `E501`s from the
  first draft fixed).
- Smoke-tested via `TestClient` with `FORGE_EDITOR_URL` unset:
  `GET /api/health` → `"editor": {"available": false, "reason": "not configured"}`;
  `GET /editor/foo` → `503` with a clear body. The positive path (editor
  actually reachable, contract match, marker match) is exercised for real
  in Phase 6/7 once both containers are running together — no value in
  faking an HTTP server here just to re-prove what `httpx` already does.

Next: [Phase 4 — host-side UI](04-host-ui.md).
