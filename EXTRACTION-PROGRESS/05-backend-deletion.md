# Phase 5 — Delete editor code from the host backend

Repo: this one (`fordge-ledger/backend`). The frontend half of "delete the
editor's own code from the host" already happened in Phase 4 (4f) — it was
a hard prerequisite for that phase's edits to typecheck at all. This phase
is the remaining backend half.

## What happened

- `git rm`: `backend/app/api/tools.py`, `backend/app/services/tools.py`,
  `backend/tests/test_tools.py`, `backend/app/resources/tools/{screw,nut}.png`,
  and the dev-tree originals at `resources/tools/{screw,nut,.DS_Store}`.
- `main.py`: dropped `tools` from the router import and from the
  `include_router` loop.
- `state.py`: dropped the `ToolsService` import, `self.tools = ToolsService(self.library)`,
  and `self.tools.ensure_defaults()` from `startup()`.
- `grep -rn "tools" backend/app` — every remaining hit is expected: MCP
  tools (`mcp_server.py`), the repo-root `tools/forge-upload.py` script
  (mentioned in `mcp_server.py`'s docstrings), a couple of unrelated
  comments ("CAD tools write in bursts"), and — deliberately kept —
  `services/images.py`'s `list_sources` still excluding
  `models/sources/tools/%` from what it returns. That exclusion isn't
  tools-service code; it's Forge Ledger's own Sources-tab query staying
  correct regardless of who last wrote into that subfolder.
- `.gitignore`: the `frontend/public/openscad/` entry was already dropped in
  Phase 4 (it had to go before that phase's frontend deletions could be
  cleanly verified). `.dockerignore` needed no change, as the plan expected
  — confirmed by reading it; nothing in it names `tools` at all.

## Data note (unchanged from the plan)

`_shared/tools/` and any existing project's `models/sources/tools/` copies
stay exactly where they are on disk. Nothing migrates. A library that has
never run the editor container gets `screw` and `nut` seeded the first time
`forge-scad-editor` boots (its own `ensure_defaults()`, moved there in
Phase 2) — same as `ensure_defaults` never overwriting behaved here before
the split.

## Verified

- `pytest -q`: **173 passed** (down from the baseline's 177 — the 4 tests
  in the now-deleted `test_tools.py`, exactly accounted for). One
  `test_failure_details_reach_the_sidecar_on_disk` failure on the first run
  reproduced as the same pre-existing `project.yaml.tmp` rename flake noted
  in Phase 3's notes (passes clean on immediate re-run; unrelated to
  anything this phase touched).
- `ruff check .`: **4 pre-existing findings**, down from the baseline's 10
  — the 6 that go away are exactly the ones that lived inside the now-deleted
  `services/tools.py` (the bare `except Exception` and five long lines in
  the `DEFAULT_TOOLS` SCAD source strings). The remaining 4 are unrelated to
  the editor and were already present in Phase 0's baseline; left alone,
  same policy as Phase 0.
- `.dockerignore` read and confirmed to need no change.

Next: [Phase 6 — compose integration](06-compose-integration.md).
