# Phase 6 — Compose integration

Repo: this one.

## `docker-compose.yml`

- `forge-ledger` gained `FORGE_EDITOR_URL: "http://forge-scad-editor:8080"`
  and a comment explaining that removing the `forge-scad-editor` service (or
  commenting out this line) is the supported way to hide the tab — the
  probe decides, not the variable (§2.4, Phase 3). No `depends_on`,
  deliberately, matching the plan's R6 reasoning: the probe already
  tolerates an editor that's absent, slow to start, or stopped later, so
  adding `depends_on` would only pin the file to Compose ≥2.20 for
  `required: false` and buy nothing.
- New `forge-scad-editor` service: `image: ghcr.io/huntler/forge-scad-editor:latest`
  (no `build:` — that image is published by forge-scad-editor's own CI,
  unlike `forge-ledger`'s `build: .`; a comment gives the local-dev
  workaround — build the sibling checkout yourself and tag it to match).
  No published port (R11 — one unauthenticated LAN surface, reached only
  through the host's `/editor/` proxy, not two). Bind-mounts
  `${LIBRARY_PATH:-./library}:/library`, the same variable the host's own
  service uses, with a comment that they must resolve to the same
  directory or the marker check (R4) reports a mismatch. Healthcheck hits
  `/editor/api/health` (not bare `/api/health` — see Phase 3's notes on why
  the editor's own API moved under `/editor/`).

## `docker-compose.test.yml`

Mirrored, with the one change the plan's checklist called out explicitly:
**`/library` is now a named volume (`library-test`), not `tmpfs`.** The
file's own pre-existing comment already documented that this
`container-compose` build ignores the `tmpfs` key and falls back to the
container's own filesystem — which is *still* per-container, not shared, so
two services each mounting "`/library`" that way would each see their own
empty directory and the marker check would (correctly, but unhelpfully for
a disposable test instance) report a mismatch. A named volume is the one
mount type that is actually shared between the two services here. `/data`
stays on `tmpfs` — only the host reads it, and the editor never needs it.
`down -v` still throws the volume away, matching "nothing you do here
survives" from the file's existing header comment.

## `.env.example`

Documented `FORGE_EDITOR_URL`, commented out (compose already sets it), with
the same "removing the service is the supported way to turn the editor off,
not blanking this var" note.

## Verified

- Both compose files parse as valid YAML with the expected service/volume
  keys (`python -c "import yaml; …"` — `container-compose` has no `config`
  subcommand to validate against directly).
- Functional verification (both containers actually reachable together,
  the marker check passing, the tab appearing/disappearing) happens in
  Phase 7, where both images are actually built and run side by side.

Next: [Phase 7 — verification](07-verification.md).
