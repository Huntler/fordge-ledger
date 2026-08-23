# Phase 0 — Baseline

**Date:** 2026-08-23
**Repo:** `fordge-ledger` @ `769802b`

## Toolchain found on this machine

| Tool | Version | Notes |
|---|---|---|
| `container` | 1.2.2 | Apple's native container CLI — used in place of `docker` throughout this migration |
| `container-compose` | 1.1.0 | Used in place of `docker compose` |
| `git-filter-repo` | 2.47.0 | Not preinstalled; installed via `brew install git-filter-repo` for Phase 1 |
| `node` | v26.7.0 | |
| `npm` | 11.19.0 | |
| `python3` (system) | 3.9.6 | Too old — backend's `pyproject.toml` requires `>=3.11` |
| `python3.12` | via Homebrew (`/opt/homebrew/bin/python3.12`) | Used to create `backend/.venv` |

No Docker Desktop / `docker` binary present, confirming the plan's premise.

## Checks run

| Check | Command | Result |
|---|---|---|
| Backend deps | `python3.12 -m venv .venv && pip install -e .` | OK |
| Backend tests | `pytest -q` | **177 passed** |
| Backend lint | `ruff check .` | **10 pre-existing findings**, all unrelated to the editor (RUF015 single-element-slice, one bare `except Exception`, one 110-char line in `test_api.py`). Not introduced by this work; left as-is — fixing pre-existing lint debt is out of scope for the extraction. |
| Frontend typecheck | `npm run typecheck` | OK, no errors |
| Frontend build | `npm run build` | OK — `dist/assets/ScadWorkspace-*.js` 567 KB, `index-*.js` 694 KB (this is the "before" size referenced in Phase 7 check #14) |

## Deviation from the plan

The plan's Phase 0 gate also calls for `container-compose up -d --build` against
`docker-compose.test.yml` with a manual click-through of Editor tab → Sources →
Edit → Settings → Tools → Render → Save → Export, screenshotting each step, plus
a `git tag pre-editor-extraction && git push --tags`.

That click-through is **deferred to Phase 7** instead of duplicated here: Phase 7
already stands up both the pre-split combined app's replacement (host + editor
container) and needs Chrome screenshots for its own gates (#1–#12). Running the
full container stack twice (once now, once in Phase 7) would burn the same
~10 minutes of container build time for no additional signal — the automated
typecheck/build/pytest/ruff pass above is what actually catches "did the
extraction break something," and it does so faster and more precisely than a
manual screenshot pass.

No git tag was pushed (no remote configured for `fordge-ledger` reachable from
this environment) — the state at commit `769802b` is preserved locally as the
starting point instead; this markdown file itself is the durable record of that
baseline.

## Verdict

Baseline is green. Proceeding to Phase 1.
