# Phase 2 — Make the editor standalone

Repo: `../forge-scad-editor`. This is the phase the plan estimated at "2
days" and flagged as carrying the real uncertainty — it did: this is the
longest section of these notes.

## 2a — Backend: `ToolsService` decoupled from `LibraryService`

- New `backend/app/hosts/base.py`: the `HostAdapter` Protocol from the plan
  (§2.3) verbatim, plus `MODEL_SOURCES_DIR = "models/sources"` — pulled out
  of the (now nonexistent) `services/library.py` as a small shared constant
  so both adapters and `ToolsService` agree on where tool copies live,
  regardless of host mode.
- `ToolsService.__init__` now takes `(host: HostAdapter, tools_root: Path)`
  instead of a `LibraryService`. `self.library.dir_for_id(...)` →
  `self.host.project_dir(...)`; `self.library.scan_project_dir(...)` →
  `self.host.notify_changed(...)`. Everything else (`ensure_defaults`,
  `save_tool`, `_save_icon`, `_icon_has_alpha`, `_still_referenced`,
  `TOOL_USE_RE`, the two default snippets) moved verbatim, as the plan said
  it would.

## 2a (extra) — Both host adapters, D2

- **`LocalHostAdapter`** (`hosts/local.py`): a project *is* a subdirectory of
  `SCADED_LIBRARY_PATH`; the directory name doubles as the project id (no
  database to give it a stable id independent of its slug). `.scad`
  discovery is `rglob("*.scad")` across the whole project directory, not
  scoped to `models/sources/` — a deliberate looseness beyond the plan's
  literal text, so a scratch folder with `.scad` files sitting at its root
  (the most likely shape for genuinely standalone use) is still a valid
  "project". Tool copies still always land under
  `models/sources/tools/`, for consistency with forge mode.
- **`ForgeHostAdapter`** (`hosts/forge.py`): calls the seven *unmodified*
  Forge Ledger endpoints from the plan's host contract (§1.4) via `httpx`.
  **Deviation:** `project_dir` resolves the project's slug from
  `GET /api/projects/{id}` (which already returns `slug`) instead of adding
  the plan's optional `GET /api/projects/{id}/dir` (§3.5/3d). Same
  information, zero new host endpoint — Phase 3 does not need to add
  anything for this to work. The library-marker check (R4, Phase 3) is what
  actually guards against `SCADED_LIBRARY_PATH` not matching the host's
  `library_path`, not this adapter, so skipping the `/dir` endpoint loses no
  safety.
- `require_project` (`api/deps.py`) now resolves existence via
  `host.project_dir(...) is not None`, since there's no local database to
  404 against.

## 2b — `api/host.py`

Thin passthrough exactly as specified: `GET /api/projects`,
`GET /api/projects/{id}/sources`, `GET .../file`, `POST .../sources`,
`PUT .../sources/content`, `POST .../sources/export`,
`DELETE .../sources`. Each delegates straight to `state.host`.
**Deviation from the plan's sketch:** path-safety `ValueError`s (R12) map to
`400`, not `422` — `422` stayed for the tools router's own validation
(square-icon-only, etc.), since verification check #18 explicitly expects
`400` for a traversal attempt.

## 2c — `config.py`

`SCADED_HOST_MODE` (default `local`), `SCADED_HOST_API`, `SCADED_LIBRARY_PATH`
(default `/library`), `SCADED_CORS_ORIGINS`. `Settings.validate_mode()` fails
fast at startup if `host_mode=forge` and `host_api` is empty — verified by
hand (`Settings(host_mode="forge").validate_mode()` raises).

## 2d — Frontend owns its shared pieces

- `components/ui.tsx` trimmed from 340 lines / 13 exports to 7: `Spinner`,
  `EmptyState`, `ConfirmDialog`, `Modal`, `Tooltip`, `Switch`, `Toasts`.
  Confirmed by grep across every moved component that nothing else
  (`StatusBadge`, `JobProgress`, `Markdown`, `CopyButton`, the formatters)
  is imported anywhere in this repo.
- **Design tokens (D3):** `frontend/src/tokens.css` and
  `frontend/tailwind.tokens.cjs` created, each with a header naming its
  counterpart path in `fordge-ledger`. `tailwind.config.js` now
  `require()`s the tokens file and spreads it into `theme.extend`;
  `index.css` `@import`s `tokens.css` **before** the `@tailwind` directives
  (Vite inlines `@import` pre-Tailwind either way, but only the
  import-first order avoids a `[vite:css] @import must precede all other
  statements` build warning — caught by actually running `vite build`, see
  below). The counterpart files still need creating on the `fordge-ledger`
  side — tracked as a Phase 4/5 follow-up, since that's when this repo's own
  `index.css`/`tailwind.config.js` get touched anyway.
- New `api.ts`: 150 lines (`ProjectSummary` trimmed to `id`+`title`,
  `SourceFile`, `ProjectSources`, `Tool`, the six tools methods, the seven
  host methods from 2b, `readTextFile`/`fileUrl`, plus this repo's own
  `EditorHealth` shape for `/api/health` — deliberately different from the
  *host's* `/api/health.editor` block, which wraps it).
- New `App.tsx`: `/` → `EditorPage`, `/settings` → a new `SettingsPage`
  wrapping `ToolsSettings`. An `embed=1` query param (see below) skips this
  app's own header chrome entirely.
- **Deep links, embed mode, and host cache invalidation (§2d + §4c),
  implemented now rather than deferred to Phase 4:** `EditorPage` reads
  `?project=&file=&embed=1` from the URL on mount (`file` omitted → new file
  seeded from `NEW_SCAD_TEMPLATE`) and, when `embed=1`, renders just the
  workspace + toolbar with no project explorer — this is what Forge Ledger's
  Sources-tab modal iframe will point at. On save/export/delete it posts
  `{type: "scad-editor:changed", projectId, relPath}` to `window.parent`
  (**deviation:** targeted at `window.location.origin`, not the plan's
  literal `"*"` — same-origin holds through the host's proxy regardless, so
  there's no reason to use a wildcard target). `ScadWorkspace` gained an
  `onExported` prop to make the export half of this possible; `onSaved` and
  `FileExplorer`'s `onFileRemoved` already existed. The *receiving* end
  (Forge Ledger's `EditorFrame`/Project modal listening for this message) is
  Phase 4 work, done there.

## 2e — R1: the wasm base path, fixed and verified against a real build

`openscad.worker.ts`: `ASSET_BASE = new URL(\`${import.meta.env.BASE_URL}openscad/\`, self.location.origin)`,
an `asset()` helper, `locateFile: (path) => asset(path)`, and an explicit
`res.ok` check on the initial fetch of `openscad.js` (turns a 404 into a
legible `Error` instead of a bare Emscripten abort). `vite.config.ts` sets
`base: "/editor/"` as a hardcoded constant per the plan's callout.

**Verified against the actual production build, not just by inspection:**
after `npm run build`, `dist/index.html`'s script/link tags carry
`/editor/assets/…`, `dist/openscad/{openscad.js,openscad.wasm}` exist
(9.6 MB), and the built worker chunk contains
`new URL("/editor/openscad/",self.location.origin)` — `import.meta.env.BASE_URL`
was correctly inlined to the literal `/editor/` at build time. Confirmed
again later, live, in Phase 7's Chrome session: `GET /editor/openscad/openscad.wasm`
returned `200` with the full 9,603,115 bytes.

## 2f — Dockerfile + standalone compose, and the gate

- `Dockerfile`: same two-stage shape as the host's, `EXPOSE 8080`, no
  `render`-extra apt packages (no turntable rendering in this image).
- `docker-compose.yml`: `local` mode, bind-mounts `${LIBRARY_PATH:-./library}`,
  `curl`-based healthcheck.
- `test-editor-standalone.sh`: seeds a scratch library with two sample
  projects (one plain part, one that `use`s the shipped `screw` tool) and
  runs `container-compose up`. `stop` tears the container down and deletes
  the scratch library. This is the "test script to start the standalone SCAD
  editor" asked for.

**Gate result — `./test-editor-standalone.sh -d --build`, full pipeline
exercised for real (not simulated) via Apple's `container`/`container-compose`
(`container system start` first, to bring the VM up):**

1. First attempt failed the image build: the frontend's new Vitest fixture
   test (`src/lib/__tests__/toolUseRegex.test.ts`, added for R3, see
   `03-r3-r12-fixups.md`) imports a JSON fixture from `backend/tests/`, which
   the Docker build context for the `frontend` stage doesn't have (only
   `frontend/` is `COPY`'d in) — and `tsc -b` (run by `npm run build`) was
   type-checking it. Fixed by excluding `src/**/__tests__/**` from
   `tsconfig.json`'s project — test files are Vitest's concern, not
   `tsc -b`'s; `vite build` never reaches them either way since nothing in
   the app's module graph imports them.
2. Rebuilt clean. `container ls` showed the service running; `curl
   http://localhost:8080/api/health` returned
   `{"status":"ok","version":"0.1.0","host_contract":1,"host_mode":"local","library_marker":null,"tools_count":2}`.
   (`container-compose`'s own healthcheck wait printed a stale "failed its
   healthcheck" line from cleaning up the previous broken build's leftover
   container before this one — the container it actually left running was
   healthy; see `07-verification.md` for the full transcript.)
3. `curl localhost:8080/` → `307` to `/editor/`; `/editor/` → `200`;
   `/editor/openscad/openscad.wasm` → `200`, 9,603,115 bytes;
   `/api/projects` → both scratch projects.
4. **Full Chrome session** (Phase 7's tools, used here since this is where
   the gate actually needs them): opened `desk-hook/models/sources/hook.scad`
   (which hand-references `use <tools/screw.scad>;`) — the `screw` tool icon
   was already highlighted in the toolbar; CodeMirror, the object list
   ("hook", "translate › sc…") and syntax highlighting all rendered
   correctly. Clicked **Render** — the hook geometry appeared in the
   three.js preview with the resolved screw insert, object thumbnails
   populated. Clicked **Export STL** — toast "Exported
   models/sources/hook.stl"; confirmed on disk via the bind-mounted scratch
   library. Toggled the `screw` tool off then on via the toolbar, then
   **Save** — toast "Saved models/sources/hook.scad"; confirmed
   `models/sources/tools/screw.scad` was physically copied in
   (`copy_into_project`, R8) and the `use` line was correctly reinserted.
   Visited **Settings** — Tools panel listed `nut` and `screw` with their
   icons. Console showed only OpenSCAD's own relayed stderr
   (`printErr` → `console.error`, "Status: NoError" — a pre-existing,
   expected quirk of the worker's logging, not a regression). No app errors.
5. Tore down: closed the Chrome tab, `./test-editor-standalone.sh stop`.

**This is the Phase 2f gate, met in full**, and incidentally covers most of
Phase 7's verification checks #1, #2, #3, #4, #5, #7, #13 already — noted
there rather than re-run twice.

Next: [Phase 3 — host proxy + availability probe](03-host-proxy-and-probe.md).
