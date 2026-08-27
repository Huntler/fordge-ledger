# Extracting the SCAD editor into a standalone project

**Status:** plan, not yet executed
**Date:** 2026-08-23
**Repo analysed:** `fordge-ledger` @ `769802b`

**Goal.** Move the in-browser OpenSCAD editor and its "tools" (reusable SCAD
snippets) out of Forge Ledger into their own git repository, shipped as their
own Docker image. Forge Ledger integrates it by adding one service to
`docker-compose.yml`. Leave that service out and the **Editor** tab disappears
from the navigation — no rebuild, no code change, no broken pages.

---

## Part 1 — How the editor is wired in today

### 1.1 What "the editor" actually is

Three distinct things are entangled under this name:

| Piece | What it does | Entry point |
|---|---|---|
| **Workspace** | CodeMirror buffer + object list + three.js STL preview + OpenSCAD-WASM render loop | `components/ScadWorkspace.tsx` |
| **Editor page** | Library-wide file explorer next to the workspace, plus the tools toolbar | `pages/Editor.tsx` → nav tab `/editor` |
| **Tools** | Library-wide `.scad` snippets with icons, stored in `<library>/_shared/tools/`, copied into a project on use | `services/tools.py` + `api/tools.py` + `ToolsSettings.tsx` |

The `ScadEditor` component is only a full-screen modal wrapper around
`ScadWorkspace` — used from a project's Sources tab. It is not a separate
editor.

### 1.2 Frontend inventory

**Owned exclusively by the editor** (nothing else in the app imports these —
verified by grep across `frontend/src`):

```
frontend/src/pages/Editor.tsx                       75 lines
frontend/src/components/ScadWorkspace.tsx          498
frontend/src/components/ScadEditor.tsx              34
frontend/src/components/ScadToolbar.tsx            129
frontend/src/components/ScadObjectList.tsx          71
frontend/src/components/StlPreview.tsx             295
frontend/src/components/FileExplorer.tsx           198
frontend/src/components/ToolForm.tsx               154
frontend/src/components/ToolsSettings.tsx          123
frontend/src/lib/scadObjects.ts                    153
frontend/src/lib/scadRenderer.ts                    78
frontend/src/lib/scadThumbnail.ts                   61
frontend/src/lib/scadTemplate.ts                     8
frontend/src/workers/openscad.worker.ts            100
frontend/src/lang-openscad/                        ~600 (builtins, completions,
                                                    highlight, language, lint,
                                                    index, openscad.grammar,
                                                    parser.js, parser.terms.js,
                                                    parser.d.ts)
frontend/scripts/fetch-openscad-wasm.mjs           103
frontend/public/openscad/{openscad.js,.wasm}       generated, gitignored
```

≈ 2 700 lines of TypeScript plus the generated Lezer parser.

**npm dependencies used *only* by the editor** (confirmed: no other file in
`frontend/src` imports `three`, `@codemirror/*`, `@lezer/*` or
`@uiw/react-codemirror`):

- runtime: `@codemirror/autocomplete`, `@codemirror/language`, `@codemirror/lint`,
  `@codemirror/view`, `@lezer/common`, `@lezer/highlight`, `@lezer/lr`,
  `@uiw/react-codemirror`, `three`
- dev: `@lezer/generator`, `@types/three`

Forge Ledger's frontend is left with `react`, `react-dom`, `react-router-dom`,
`@tanstack/react-query`, `zustand`. That is a clean cut — the heaviest half of
the dependency tree leaves with the editor.

**Shared frontend infrastructure the editor *borrows*** (used by the rest of the
app too — these must be duplicated, not moved):

| Import | Used by | Notes |
|---|---|---|
| `components/ui.tsx` → `Spinner`, `EmptyState`, `ConfirmDialog`, `Modal`, `Tooltip`, `Switch` | all editor components | 6 of 13 exports; the rest (StatusBadge, Toasts, JobProgress, Markdown, CopyButton, formatters) stay |
| `components/DropZone.tsx` | `ToolForm` (icon upload) | 93 lines |
| `store.ts` → `useUi().notify` | ScadWorkspace, FileExplorer, ToolForm, ToolsSettings | toast bus |
| `api.ts` | everything | typed REST client, see §1.4 |
| `index.css` + `tailwind.config.js` | everything | `.card` `.btn` `.btn-primary` `.btn-ghost` `.input` `.label` `.chip`, and the `ink-{900..500}` / `accent` palette |

### 1.3 Backend inventory

**Owned exclusively by the editor:**

```
backend/app/api/tools.py                    61 lines   — /api/tools/* router
backend/app/services/tools.py              450 lines   — ToolsService + the two
                                                         default tools (screw, nut)
backend/app/resources/tools/{screw,nut}.png            — seeded default icons
backend/tests/test_tools.py                            — per-project copy semantics
resources/tools/{screw,nut}.png                        — dev-tree originals
```

Wiring points in the host backend:

- `app/main.py:15` — `from .api import … tools`
- `app/main.py:72` — `tools.router` in the `include_router` loop
- `app/state.py:16,39` — `self.tools = ToolsService(self.library)`
- `app/state.py:92` — `self.tools.ensure_defaults()` on startup

`ToolsService` depends on `LibraryService` for exactly two things:
`dir_for_id(project_id) -> Path` and `scan_project_dir(directory)`. That is the
entire backend coupling surface, and it is the seam to cut on.

### 1.4 The API contract the editor consumes

These endpoints are **not** editor-owned — they are Forge Ledger's file/project
API, and the editor is a client of them. They become the *host contract*:

| Endpoint | Used for |
|---|---|
| `GET /api/projects` | FileExplorer's project list |
| `GET /api/projects/{id}/sources` | `.scad` files per project (excludes `models/sources/tools/`) |
| `GET /api/projects/{id}/file?rel_path=` | raw text of a source (`readTextFile`) |
| `POST /api/projects/{id}/models/sources` | create a new `.scad` (multipart) |
| `PUT /api/projects/{id}/models/sources/content?rel_path=` | Save (overwrite in place) |
| `POST /api/projects/{id}/models/sources/export?rel_path=` | Export STL next to the source |
| `DELETE /api/projects/{id}/models/sources?rel_path=` | FileExplorer's ✕ |

And these are editor-owned, moving out:

| Endpoint | Purpose |
|---|---|
| `GET /api/tools` | list snippets (`name`, `body`, `has_icon`, `has_alpha`) |
| `PUT /api/tools` | create/update (multipart: name, body, optional square PNG ≤512²) |
| `DELETE /api/tools/{name}` | delete snippet + icon |
| `GET /api/tools/{name}/icon` | PNG |
| `POST /api/tools/{name}/projects/{project_id}` | copy `.scad` into `models/sources/tools/` |
| `DELETE /api/tools/{name}/projects/{project_id}` | remove copy *if no other source still references it* |

### 1.5 Coupling points that must change in Forge Ledger

| File | Line(s) | What |
|---|---|---|
| `frontend/src/App.tsx` | 11, 17, 27, 81 | `EditorPage` import, `NAV` entry, `isEditor` full-width rule, `/editor` route |
| `frontend/src/pages/Project.tsx` | 16, 18–20, ~1057–1071 | `NEW_SCAD_TEMPLATE` import, lazy `ScadEditor` import, the `<ScadEditor>` modal, the "Edit"/"+ New .scad" actions |
| `frontend/src/pages/Settings.tsx` | 5, 75 | `ToolsSettings` import + render |
| `frontend/src/api.ts` | 180–187, 480–501 | `Tool` interface and the six tools methods |
| `frontend/src/components/ui.tsx` | 277 | comment referencing `ScadToolbar` |
| `frontend/package.json` | scripts + deps | `predev`/`prebuild` wasm fetch, `grammar:build`, 11 packages |
| `backend/app/main.py` | 15, 72 | tools router |
| `backend/app/state.py` | 16, 39, 92 | `ToolsService` construction + seeding |
| `.gitignore` | | `frontend/public/openscad/` |
| `README.md` | ~421–480 | § "The in-browser SCAD editor" |
| `Dockerfile` | frontend stage | no structural change; build gets materially faster |

### 1.6 Behaviours that must survive the split

Details that are easy to lose and expensive to rediscover:

1. **Two parsers of the same regex.** `TOOL_USE_RE` exists twice —
   `ScadWorkspace.tsx:20` (TS) and `services/tools.py:29` (Python), kept in sync
   by hand. Both must land in the *same* repo after the split so they can stay
   in sync.
2. **A tool copy is project-scoped, not file-scoped.** `remove_from_project`
   only deletes `models/sources/tools/<slug>.scad` once no *other* `.scad` in
   the project still references it (`_still_referenced`). `test_tools.py` covers
   this regression specifically.
3. **The live renderer reads tool bodies from `_shared/tools/`**, not from the
   per-project copy — `extractToolFiles` in `ScadWorkspace.tsx` stages them into
   the worker's virtual FS. The on-disk copy exists so a saved `.scad` still
   resolves *outside* this app.
4. **Sibling `.scad` files are staged into the VFS on every render**
   (`loadProjectFiles`), keyed by their path relative to `models/sources/`, and
   tool references are scanned across *all* staged sources, not just the open
   buffer.
5. **A fresh WASM module instance per render is mandatory.** A second
   `callMain()` on the same instance reliably aborts. Documented in
   `openscad.worker.ts` and README.
6. **The WASM build is fetched, not vendored** — pinned by URL + sha256 in
   `fetch-openscad-wasm.mjs`, unpacked with a hand-rolled ZIP reader so no
   system `unzip` is needed in the Alpine build stage.
7. **`list_sources` deliberately hides `models/sources/tools/`.** If the editor
   ever serves its own listing, it must reproduce that exclusion.

---

## Part 2 — Target architecture

### 2.1 The decision

Three integration styles were considered:

| Option | Runtime-optional? | Effort | Verdict |
|---|---|---|---|
| **A. Separate container, own SPA, embedded via iframe through a host reverse proxy** | Yes — nothing editor-shaped is in the host bundle | Medium | **Recommended** |
| B. Editor published as an npm package the host imports | No — removing the container breaks the host build | Low | Fails the requirement |
| C. Runtime ESM/module federation into the host's React tree | Yes | High | Vite needs a federation plugin; React must be externalised and version-locked across two repos. Revisit only if the iframe seams become annoying |

**Option A** is the one that actually satisfies "leave it out of compose and the
tab hides": the host's JavaScript bundle never contains a byte of editor code,
so its absence is a runtime fact, not a build error.

### 2.2 Shape

```
                    ┌─────────────────────────── forge-ledger container ──┐
   browser ─────────┤  FastAPI :8000                                      │
                    │    /api/…            library, projects, files, prints│
                    │    /editor/*  ──────────┐  httpx streaming proxy     │
                    │    /            SPA     │                            │
                    └─────────────────────────┼────────────────────────────┘
                                              │  (docker network)
                    ┌──────────────────▼── forge-scad-editor container ────┐
                    │  FastAPI :8080   (port NOT published — see R11)      │
                    │    /api/tools/…      ToolsService over /library      │
                    │    /api/health       incl. host_contract version     │
                    │    /editor/          editor SPA (base baked at build)│
                    │  volumes: /library (rw)                              │
                    │  env: SCADED_HOST_MODE=forge                         │
                    │       SCADED_HOST_API=http://forge-ledger:8000       │
                    └──────────────────────────────────────────────────────┘
```

- The editor container mounts **the same library volume** — it needs
  `_shared/tools/` and `models/sources/tools/` on disk.
- For project listing and file read/write it calls **Forge Ledger's REST API**,
  so the SQLite index and the filesystem watcher keep a single owner. This is
  the `forge` host mode.
- A `local` host mode (walk a mounted directory tree directly, no host API)
  keeps the project genuinely standalone — `docker compose up` in the editor
  repo alone gives a working OpenSCAD editor over any folder. **Ships in v1**
  (decision D2, §4.1).

### 2.3 Host-adapter interface

Define this once in the editor repo; two implementations behind it.

```python
class HostAdapter(Protocol):
    def list_projects(self) -> list[Project]: ...          # id, title
    def list_scad_sources(self, project_id) -> list[SourceFile]: ...
    def read_source(self, project_id, rel_path) -> str: ...
    def write_source(self, project_id, rel_path, text) -> None: ...
    def create_source(self, project_id, filename, text) -> str: ...
    def delete_source(self, project_id, rel_path) -> None: ...
    def export_stl(self, project_id, rel_path, stl: bytes) -> str: ...
    def project_dir(self, project_id) -> Path: ...          # for tool copies
    def notify_changed(self, project_id) -> None: ...       # host rescan hook
```

- `ForgeHostAdapter` — HTTP against `SCADED_HOST_API`; `project_dir` resolves
  via a new host endpoint (see §3.5); `notify_changed` calls
  `POST /api/projects/{id}/rescan`.
- `LocalHostAdapter` — every project is a subdirectory of `SCADED_LIBRARY_PATH`;
  `project_dir` is that directory; `notify_changed` is a no-op.

The frontend keeps talking to *one* API — the editor's own — and the editor
backend fans out. This is the important simplification: the editor SPA never
needs to know which mode it is in, and no CORS is involved anywhere.

### 2.4 Availability & tab visibility

1. Forge Ledger gains one setting: `FORGE_EDITOR_URL` (default `""`).
2. On startup and every 60 s, the backend probes `GET {FORGE_EDITOR_URL}/api/health`
   with a 2 s timeout, caching the result.
3. `GET /api/health` grows a block:
   ```json
   "editor": {
     "available": true,
     "path": "/editor/",
     "version": "0.1.0",
     "host_contract": 1,
     "library_path": "/library",
     "reason": null
   }
   ```
4. `App.tsx` builds `NAV` from `available`. Absent or unreachable → no tab, no
   route, nothing lazy-loaded.

The **probe** is authoritative, not the env var. A `FORGE_EDITOR_URL` left
behind after the service is removed therefore still hides the tab, instead of
producing a dead link.

`available` is false — with a human-readable `reason` — in four cases: no
`FORGE_EDITOR_URL`, probe timeout/refused, `host_contract` mismatch (R9), or
`library_path` mismatch (R4). One flag drives the UI; `reason` drives the log
line and the Settings row.

---

## Part 3 — Migration steps

Each phase ends at a state where both repos build and the app runs. Do not
collapse them.

### Phase 0 — Baseline

- [ ] `cd frontend && npm run typecheck && npm run build`
- [ ] `cd backend && python -m pytest -q && ruff check .`
- [ ] `docker compose -f docker-compose.test.yml up -d --build`, click through:
      Editor tab, project Sources → Edit, Settings → Tools, tool toggle,
      Render, Save, Export STL. **Screenshot each.** These are the acceptance
      shots for Phase 7.
- [ ] `git tag pre-editor-extraction && git push --tags`

### Phase 1 — Create the editor repo with history

`git filter-repo` keeps blame on ~2 700 lines of nontrivial code. Worth the ten
minutes.

```bash
git clone git@github.com:Huntler/fordge-ledger.git /tmp/forge-scad-editor
cd /tmp/forge-scad-editor
git filter-repo \
  --path frontend/src/pages/Editor.tsx \
  --path frontend/src/components/ScadWorkspace.tsx \
  --path frontend/src/components/ScadEditor.tsx \
  --path frontend/src/components/ScadToolbar.tsx \
  --path frontend/src/components/ScadObjectList.tsx \
  --path frontend/src/components/StlPreview.tsx \
  --path frontend/src/components/FileExplorer.tsx \
  --path frontend/src/components/ToolForm.tsx \
  --path frontend/src/components/ToolsSettings.tsx \
  --path frontend/src/components/DropZone.tsx \
  --path frontend/src/components/ui.tsx \
  --path frontend/src/store.ts \
  --path frontend/src/index.css \
  --path frontend/src/lang-openscad \
  --path frontend/src/lib/scadObjects.ts \
  --path frontend/src/lib/scadRenderer.ts \
  --path frontend/src/lib/scadThumbnail.ts \
  --path frontend/src/lib/scadTemplate.ts \
  --path frontend/src/workers/openscad.worker.ts \
  --path frontend/scripts/fetch-openscad-wasm.mjs \
  --path frontend/tailwind.config.js \
  --path frontend/postcss.config.js \
  --path backend/app/api/tools.py \
  --path backend/app/services/tools.py \
  --path backend/app/resources/tools \
  --path backend/app/utils.py \
  --path backend/tests/test_tools.py
```

Then restructure into the new layout and push to a fresh
`git@github.com:Huntler/forge-scad-editor.git`:

```
forge-scad-editor/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app, SPA mount, /api/health
│   │   ├── config.py          # SCADED_* settings
│   │   ├── api/tools.py       # from fordge-ledger
│   │   ├── api/host.py        # projects/sources proxy for the SPA
│   │   ├── services/tools.py  # ToolsService, LibraryService dep replaced
│   │   ├── hosts/base.py      # HostAdapter protocol
│   │   ├── hosts/forge.py     # HTTP adapter
│   │   ├── hosts/local.py     # filesystem adapter
│   │   ├── resources/tools/{screw,nut}.png
│   │   └── utils.py           # slugify, safe_join
│   ├── tests/
│   └── pyproject.toml
├── frontend/
│   ├── src/                   # everything above + a thin App.tsx
│   ├── scripts/fetch-openscad-wasm.mjs
│   ├── vite.config.ts         # base: "/editor/"
│   ├── tailwind.config.js
│   └── package.json
├── Dockerfile
├── docker-compose.yml         # standalone (local host mode)
├── .github/workflows/docker-publish.yml
└── README.md
```

- [ ] `git commit` the restructure as one "move files into place" commit so the
      rename detection stays readable.

### Phase 2 — Make the editor standalone

**2a. Backend — break the `LibraryService` dependency.**

`ToolsService.__init__` currently takes a `LibraryService`. Replace with the
adapter:

```python
class ToolsService:
    def __init__(self, host: HostAdapter, tools_root: Path):
        self.host = host
        self.tools_root = tools_root   # <library>/_shared/tools
```

- `self.library.root / TOOLS_DIR` → `self.tools_root`
- `self.library.dir_for_id(project_id)` → `self.host.project_dir(project_id)`
- `self.library.scan_project_dir(directory)` → `self.host.notify_changed(project_id)`

Everything else in that file — `ensure_defaults`, `save_tool`, `_save_icon`,
`_icon_has_alpha`, `_still_referenced`, `TOOL_USE_RE`, the two default snippets
— is already dependency-free and moves verbatim.

**2b. Backend — new `api/host.py`.**

Thin passthrough so the SPA has one origin and one client:

```
GET    /api/projects
GET    /api/projects/{id}/sources
GET    /api/projects/{id}/file?rel_path=
POST   /api/projects/{id}/sources          (multipart create)
PUT    /api/projects/{id}/sources/content?rel_path=
POST   /api/projects/{id}/sources/export?rel_path=
DELETE /api/projects/{id}/sources?rel_path=
```

Each delegates to the adapter. In `forge` mode these are one `httpx` call each;
in `local` mode they are filesystem operations. Reuse `safe_join` from
`utils.py` for every `rel_path` — the host's path-traversal guard does not
protect the local adapter.

**2c. Backend — `config.py`:**

| Setting | Default | Notes |
|---|---|---|
| `SCADED_HOST_MODE` | `local` | `local` \| `forge` |
| `SCADED_HOST_API` | `""` | required when mode is `forge` |
| `SCADED_LIBRARY_PATH` | `/library` | where `_shared/tools/` lives, both modes |
| `SCADED_CORS_ORIGINS` | `["http://localhost:5174"]` | dev only |

Fail fast on boot if `mode=forge` and `SCADED_HOST_API` is empty.

> **The base path is deliberately *not* here.** `/editor/` is baked in at build
> time via Vite's `base`, because `import.meta.env.BASE_URL` — which the wasm
> loader depends on (§2e, R1) — is a static build-time replacement. Making it a
> runtime variable would require serving a rewritten `<base href>` and a
> `basename`-aware router for no practical gain. The container serves the SPA at
> `/editor/` in both host modes and `302`s `/` → `/editor/`.

**2d. Frontend — own the shared pieces.**

- Trim the copied `ui.tsx` to `Spinner`, `EmptyState`, `ConfirmDialog`, `Modal`,
  `Tooltip`, `Switch`. Drop `StatusBadge`, `Toasts`… wait — `Toasts` **is**
  needed (the editor raises its own toasts through `useUi`). Keep `Toasts` and
  the formatters it uses; drop `JobProgress`, `Markdown`, `CopyButton`,
  `StatusBadge`, and the `ProjectStatus`/`PrintStatus` imports they pull in.
- **Design tokens (decision D3).** Rather than copying `tailwind.config.js` and
  `index.css` wholesale, first extract the shared surface in *both* repos into
  two files that are byte-identical across them:
  - `frontend/src/tokens.css` — the `@layer base` body/scrollbar rules and the
    `@layer components` block (`.card` `.btn` `.btn-primary` `.btn-ghost`
    `.input` `.label` `.chip`), imported by each repo's `index.css`
  - `frontend/tailwind.tokens.cjs` — the `ink-{900..500}` / `accent` palette and
    the font stacks, spread into each repo's `theme.extend`

  Each file opens with a header comment naming its counterpart path in the other
  repo. Everything repo-specific (Forge Ledger's `polish-sweep` keyframes, the
  editor's future additions) stays out of them. The fork is accepted, but it is
  now confined to two small files that `diff` cleanly instead of being smeared
  across two configs.
- Write a new `src/api.ts` containing only what the editor needs — `Tool`,
  `ProjectSummary` (id + title only), `ProjectSources`, `SourceFile`,
  `readTextFile`, `fileUrl`, the six tools methods, and the seven host methods
  from 2b. Roughly 150 lines instead of 555.
- New `App.tsx`: routes `/` → the Editor page, `/settings` → a page wrapping
  `ToolsSettings`, plus `<Toasts />`. Read the deep-link query params
  (`?project=…&file=…&embed=1`) here.
- `index.html` title → "SCAD Editor".

**2e. Frontend — fix the base path.** *(resolves R1)*

Verified against the actual Emscripten build in `public/openscad/openscad.js`:

```js
locateFile(path){ if(Module["locateFile"]) return Module["locateFile"](path) … }
if (!Module["locateFile"]) { wasmBinaryFile = new URL("openscad.wasm", import.meta.url).href }
```

Two consequences that shape the fix:

1. `locateFile` is called with the **bare string** `"openscad.wasm"` — it must
   return a fully-resolved URL, not a suffix.
2. The no-`locateFile` fallback resolves against `import.meta.url`, which for us
   is a **Blob URL** (`blob:http://host/<uuid>`) because `loadOpenSCAD()` imports
   the module from a Blob to get past Vite's `public/` restriction. So the
   `locateFile` override is **load-bearing, not a convenience** — deleting it
   does not fall back gracefully, it breaks the wasm fetch outright.

The change, in `openscad.worker.ts` (`BASE_URL` always carries a trailing slash):

```ts
// Vite bakes BASE_URL in at build time; "/editor/" in every shipped image.
const ASSET_BASE = new URL(`${import.meta.env.BASE_URL}openscad/`, self.location.origin);
const asset = (name: string) => new URL(name, ASSET_BASE).href;

async function loadOpenSCAD() {
  const res = await fetch(asset("openscad.js"));
  // Turns the top opaque failure mode into a legible one: without this, a 404
  // here surfaces later as an Emscripten abort with a bare pointer.
  if (!res.ok) throw new Error(`OpenSCAD runtime not found at ${asset("openscad.js")} (${res.status})`);
  const blobUrl = URL.createObjectURL(new Blob([await res.text()], { type: "text/javascript" }));
  …
}

// in createInstance():
locateFile: (path: string) => asset(path),
```

- `vite.config.ts` — `base: "/editor/"` (a constant, see the callout in 2c). Dev
  proxy targets the editor's own backend on `:8080`; dev server on port `5174`
  so it runs alongside Forge Ledger's `5173`.
- `main.tsx` / router — `basename={import.meta.env.BASE_URL}` so client-side
  routes sit under the same prefix.

**2f. Dockerfile.** Same two-stage shape as Forge Ledger's — node builds the
SPA, python serves it. Two differences: no Pillow-adjacent apt packages beyond
`curl` (Pillow *is* needed, for icon validation), and `EXPOSE 8080`.

- [ ] **Gate:** `docker compose up` in the editor repo alone, over a scratch
      directory with two folders containing `.scad` files. Open, edit, render,
      save, export, create a tool, toggle it.

### Phase 3 — Host-side proxy and availability

In `fordge-ledger/backend`:

**3a. `config.py`** — add `editor_url: str = ""` (→ `FORGE_EDITOR_URL`).

**3b. New `api/editor.py`** — streaming reverse proxy, using the already-present
`httpx` dependency:

```python
router = APIRouter(prefix="/editor", tags=["editor"])

@router.api_route("/{path:path}", methods=["GET","POST","PUT","DELETE","PATCH"])
async def proxy(state: State, path: str, request: Request):
    ...  # stream request → editor, stream response back, preserve headers
```

Points to get right:
- **Stream both directions.** `openscad.wasm` is ~9 MB; do not buffer it.
- Preserve `Content-Type`, `Content-Length`, `ETag`, `Cache-Control` so the
  browser caches the wasm.
- Mount this router **before** the SPA catch-all in `main.py:89`, which would
  otherwise swallow `/editor/*` — same ordering rule the MCP mount already
  follows (`main.py:76–84`).
- Return `503` with a clear body when `editor_url` is empty or the probe is
  failing, rather than a connection error.

**3c. Availability probe** — a small cached checker in `state.py` (60 s TTL,
2 s timeout). Surface it in `api/system.py:health()`:

```python
"editor": state.editor_status(),   # see the schema in §2.4
```

The probe does three checks, not one *(resolves R9, R4)*:

```python
HOST_CONTRACT_VERSION = 1   # bump on ANY change to the 7 endpoints in §1.4

def editor_status(self) -> dict:
    if not self.settings.editor_url:
        return {"available": False, "reason": "not configured"}
    try:
        body = httpx.get(f"{self.settings.editor_url}/api/health", timeout=2.0).json()
    except Exception as exc:
        return {"available": False, "reason": f"unreachable: {exc}"}
    if body.get("host_contract") != HOST_CONTRACT_VERSION:
        return {"available": False, "reason":
                f"contract mismatch: editor wants v{body.get('host_contract')}, "
                f"this server speaks v{HOST_CONTRACT_VERSION}"}
    if body.get("library_marker") != self.library_marker():
        return {"available": False, "reason":
                "library mismatch: the editor is not mounted on this library"}
    return {"available": True, "path": "/editor/", **body}
```

**The library check compares a marker, not a path.** Forge Ledger writes
`<library>/_shared/.forge-instance` containing a UUID on first startup
(create-if-missing, never overwrite); the editor's `/api/health` echoes back
whatever it reads at that path. Comparing `library_path` strings would have been
worthless: two containers can both mount `/library` and still be looking at
different filesystems — which is exactly what `docker-compose.test.yml`'s tmpfs
mounts would do. The marker catches that, an empty mount, and a genuinely wrong
path, all with one comparison.

Log the `reason` at WARNING on every transition, not on every probe — a 60 s
poll that logs each failure buries the signal.

**3d. Optional: `GET /api/projects/{id}/dir`** returning the absolute project
directory, so `ForgeHostAdapter.project_dir` does not have to guess from the
slug. Small, and it removes the last piece of filesystem-layout knowledge from
the editor's `forge` adapter.

- [ ] **Gate:** with the editor container running, `curl localhost:8000/editor/api/health`
      returns the editor's health, and `curl localhost:8000/api/health | jq .editor`
      shows `available: true`. Stop the editor container → `available: false`
      within 60 s.

### Phase 4 — Host-side UI

**4a. `App.tsx`** — conditional nav and route:

```tsx
const { data: health } = useQuery({ queryKey: ["health"], queryFn: api.health });
const NAV = [
  { to: "/library",  label: "Library" },
  { to: "/prints",   label: "Prints" },
  ...(health?.editor?.available ? [{ to: "/editor", label: "Editor" }] : []),
  { to: "/settings", label: "Settings" },
];
```

The `/editor` route renders a new ~30-line `pages/EditorFrame.tsx` — a
full-bleed `<iframe src={health.editor.path}>`. Keep the existing `isEditor`
full-width rule (`App.tsx:27`), it still applies. If the flag is false, the
route redirects to `/library` (a bookmarked `/editor` must not render a blank
frame).

**4b. `pages/Project.tsx`** — the Sources tab's Edit / + New .scad actions.
Replace the lazy `ScadEditor` import with a modal iframe pointing at
`/editor/?project={id}&file={relPath}&embed=1`; `&file=` omitted means "new
file, seeded from the template". Gate both buttons on
`health?.editor?.available` — when the editor is absent the Sources tab still
lists, downloads and deletes `.scad` files, it just cannot edit them.

Deleting `lib/scadTemplate.ts` from the host is what makes the "+ New .scad"
button need the editor. That is correct: without the editor there is nothing
to open.

**4c. Cache coherence across the iframe.** When the editor saves a file, the
host's react-query cache (`["sources", projectId]`, `["project", projectId]`)
goes stale. Have the editor `postMessage` on save/delete/export:

```js
parent.postMessage({ type: "scad-editor:changed", projectId, relPath }, "*");
```

and have `EditorFrame` / the Project modal listen and invalidate. Small, and it
avoids a class of "my file didn't show up" confusion. Verify the message
`origin` against `location.origin` — same-origin holds because everything goes
through the proxy.

**4d. `pages/Settings.tsx`** — remove the `ToolsSettings` section (lines 5, 75).
Tool management now lives at `/editor/settings`. Optionally add a one-line
link there when the editor is available.

**4e. `api.ts`** — delete the `Tool` interface and the six tools methods; add
the `editor` block to the `Health` interface.

**4f. `package.json`** — remove the 11 editor-only packages, the `predev`/
`prebuild` wasm-fetch hooks, and `grammar:build`. Run `npm install` and commit
the `package-lock.json` shrink.

- [ ] **Gate:** `npm run typecheck` passes with `noUnusedLocals` — it will catch
      any import left dangling.

### Phase 5 — Delete from the host backend

- [ ] `git rm backend/app/api/tools.py backend/app/services/tools.py`
- [ ] `git rm -r backend/app/resources/tools resources/tools`
- [ ] `git rm backend/tests/test_tools.py`
- [ ] `main.py`: drop `tools` from the import on line 15 and the router tuple
- [ ] `state.py`: drop the `ToolsService` import, `self.tools = …`, and
      `self.tools.ensure_defaults()`
- [ ] `grep -rn "tools" backend/app` — expect hits only in `mcp_server.py`,
      where "tools" means MCP tools and `tools/forge-upload.py`. Unrelated.
- [ ] `.gitignore`: drop `frontend/public/openscad/`
- [ ] `.dockerignore`: no change needed
- [ ] `python -m pytest -q && ruff check .`

**Data note:** `_shared/tools/` stays exactly where it is, inside the library.
Nothing migrates on disk, and an existing library keeps its tools and its
per-project copies. Only the process that writes them changes. `ensure_defaults`
moves to the editor's startup, so a library that has never seen the editor gets
`screw` and `nut` the first time the editor container boots — same as today.

### Phase 6 — Compose integration

`docker-compose.yml` gains one service and one env line:

```yaml
services:
  forge-ledger:
    # …unchanged…
    environment:
      # Comment this out (or drop the forge-scad-editor service) to hide the tab.
      FORGE_EDITOR_URL: "http://forge-scad-editor:8080"
    # No depends_on, deliberately (R6): the probe already handles an editor that
    # is absent, slow to start, or stopped later. Adding it would pin the file to
    # Compose >= 2.20 for `required: false` and buy nothing.

  forge-scad-editor:
    image: ghcr.io/huntler/forge-scad-editor:latest
    container_name: forge-scad-editor
    restart: unless-stopped
    # No published port, deliberately (R11): rw access to the whole library with
    # no auth. Reached only through forge-ledger's /editor/ proxy, so the LAN
    # sees one unauthenticated surface rather than two.
    volumes:
      # MUST resolve to the same directory as forge-ledger's /library, or the
      # host's probe reports a library mismatch and hides the tab (R4).
      - "${LIBRARY_PATH:-./library}:/library"
    environment:
      SCADED_HOST_MODE: "forge"
      SCADED_HOST_API: "http://forge-ledger:8000"
      SCADED_LIBRARY_PATH: "/library"
      TZ: "${TZ:-Europe/Berlin}"
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8080/api/health"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 20s
```

**Opting out** is then exactly what the requirement asks: delete (or comment
out) the `forge-scad-editor` service. With no `depends_on`, the host starts
regardless; the probe fails; the tab is not rendered. Leaving `FORGE_EDITOR_URL`
set is harmless — the probe, not the variable, decides.

- [ ] Mirror the change into `docker-compose.test.yml`. **Replace the tmpfs
      `/library` mount with a named volume shared by both containers** — a tmpfs
      is per-container, so the two would see different filesystems and the marker
      check (3c) would correctly refuse to enable the editor. A named volume
      still throws the data away on `down -v`, which is all the test instance
      needs.
- [ ] `.env.example`: document `FORGE_EDITOR_URL`, including that removing the
      service is the supported way to turn the editor off.

### Phase 7 — Verification

Run against a **copy** of a real library, not the real one.

| # | Check |
|---|---|
| 1 | Both services up → Editor tab present; explorer lists every project's `.scad` files |
| 2 | Open a file, edit, **Render** — STL preview appears; object list shows thumbnails |
| 3 | **Save** → file changes on disk; host's Sources tab reflects it without a manual refresh (postMessage path) |
| 4 | **Export STL** → `.stl` lands next to the `.scad`, host picks it up |
| 5 | Toggle a tool on → `use <tools/screw.scad>;` inserted **and** `models/sources/tools/screw.scad` created; render resolves it |
| 6 | Toggle off from source A while source B still references it → copy survives (the `_still_referenced` regression) |
| 7 | Settings → Tools: create a tool with a square PNG icon; non-square rejected client- and server-side |
| 8 | A line-art (alpha) icon renders tinted; a flat icon renders as-is (`has_alpha`) |
| 9 | Project → Sources → **Edit** opens the modal iframe on the right file |
| 10 | New `.scad` from the explorer's **+** saves under a chosen name |
| 11 | **Stop the editor container.** Within 60 s: tab gone; `/editor` redirects to `/library`; Sources tab loses Edit/+New but still lists and deletes; Settings has no Tools section; no console errors |
| 12 | **Remove the service from compose, `up -d` again.** Same as 11, from a cold start |
| 13 | Editor repo alone: `docker compose up` in `local` mode over a scratch folder — full edit/render/save/tool cycle works |
| 14 | Host image size dropped (three.js + CodeMirror + Lezer + the 9 MB wasm are gone from the host bundle); host `npm run build` is measurably faster |
| 15 | `wasm` loads **through the proxy** and is served from browser cache on a second visit (`Cache-Control: immutable` survived) — check on `/editor/`, never on `:8080` directly (R1) |
| 16 | Bump the editor's `host_contract` to `2`, redeploy only the editor → tab hides, Settings shows "contract mismatch", host logs it once (R9) |
| 17 | Point the editor at a *different* library volume → tab hides with "library mismatch"; marker check fires even though both paths read `/library` (R4) |
| 18 | `local` mode: request `rel_path=../../etc/passwd` against every write endpoint → `400`, nothing written outside the root (R12) |
| 19 | Shared-fixture test passes in both languages, including the `use <tools/x.scad>; cube(10);` case that currently diverges (R3) |
| 20 | Open the same `.scad` in the Editor tab and in a project's Edit modal, save from both → the stale-write guard warns rather than silently clobbering (R10) |

### Phase 8 — Documentation

- [ ] `fordge-ledger/README.md`: replace § "The in-browser SCAD editor" with a
      short section — what the Editor tab is, that it comes from a separate
      image, how to enable/disable it, link to the new repo. Move the WASM /
      Manifold / grammar / "fresh instance per render" prose to the editor
      repo's README verbatim; it is genuinely good documentation and belongs
      next to the code it describes.
- [ ] `forge-scad-editor/README.md`: both host modes, the env table, the compose
      snippet for embedding, the host API contract from §1.4 marked as the
      frozen surface behind `host_contract` (D4), and the duplicated-design-tokens
      caveat naming the counterpart files (D3).
- [ ] Add `.github/workflows/docker-publish.yml` to the editor repo — copy
      Forge Ledger's, change `IMAGE_NAME`. Same amd64-on-main /
      multi-arch-on-tag split.
- [ ] Cross-link the two repos in both READMEs.

---

## Part 4 — Risks, resolved

Every item below is either **resolved** (the mitigation is real work, scheduled
into a phase, with a verification step) or **accepted** (a conscious decision to
live with it, recorded so it does not get rediscovered as a surprise). Nothing
here is left open.

### 4.1 Decisions taken

| # | Decision | Rationale | Changes |
|---|---|---|---|
| **D1** | Repo is **`forge-scad-editor`**, image `ghcr.io/huntler/forge-scad-editor` | Keeps the lineage visible. The project is technically standalone (D2) but was born here and its `forge` host mode is the primary deployment | Phase 1 clone target and layout; Phase 6 service name and image |
| **D2** | The **`local` host adapter ships in v1** | Without it "standalone" is aspirational — the repo could not run alone. It also gives a host-free way to reproduce editor bugs, which is worth the day on its own | Phase 2a/2b build both adapters; adds R12 as live work; verification #13 and #18 |
| **D3** | Design tokens are **duplicated into two isolated files**, not shared via a package | A third repo plus a publish step and a version bump for every palette tweak is not worth it at this size. Confining the fork to two `diff`-able files gets most of the benefit for none of the infrastructure | Phase 2d extracts `tokens.css` + `tailwind.tokens.cjs` in **both** repos |
| **D4** | **Contract version + host-side check** guards against drift | ~20 lines, and it catches the silent-break case even when both images run `:latest`. Tag pinning relies on discipline and gives no signal when you forget | Phase 3c probe; `HOST_CONTRACT_VERSION` in host config; verification #16 |

### 4.2 Resolved risks

| ID | Risk | Resolution | Lands in | Verified by |
|---|---|---|---|---|
| **R1** | `/editor/` base path breaks wasm loading | Base baked at build time; `locateFile`/`fetch` resolve through `import.meta.env.BASE_URL`; explicit `res.ok` check turns a 404 into a legible error | Phase 2c, 2e | #15 |
| **R3** | `TOOL_USE_RE` duplicated across TS and Python | Both copies now in one repo; shared JSON fixture driving a test in each language. **Already divergent today** — see below | Phase 2a | #19 |
| **R4** | The two containers mount different libraries | Marker file `_shared/.forge-instance`, echoed by the editor's health and compared by the host — catches wrong path, empty mount, and same-path-different-filesystem | Phase 3c | #17 |
| **R5** | 9.6 MB wasm streamed through the proxy | Content is pinned by sha256 and never changes within an image → serve `Cache-Control: public, max-age=31536000, immutable`; proxy passes caching headers through untouched | Phase 3b | #15 |
| **R6** | `depends_on: required: false` needs Compose ≥ 2.20 | Dropped `depends_on` entirely. The probe already handles absent/slow/stopped, so it bought nothing but a version floor | Phase 6 | #11, #12 |
| **R8** | Editor writes to the library outside the host's DB | `notify_changed()` is mandatory in the `HostAdapter` protocol and called after every mutation; in `forge` mode it hits the existing `POST /api/projects/{id}/rescan`. The filesystem watcher is the second net | Phase 2a, 2b | #5, #6 |
| **R9** | The two images drift apart silently | `host_contract` integer in the editor's health, compared by the host's probe; mismatch hides the tab with a logged reason rather than failing at click time | Phase 3c | #16 |
| **R12** | Path traversal in `local` mode | `forge` mode inherits the host's `safe_join`; `local` mode has no such protection and must apply `safe_join` to every `rel_path`. Live work now that D2 puts local mode in v1 | Phase 2b | #18 |

**R3 in detail — the two regexes are already out of sync.** Both files carry a
"kept in sync by hand" comment, and both are wrong about that:

```
TS  (ScadWorkspace.tsx:20)  …tools\/([a-z0-9-]+)\.scad[ \t]*>[ \t]*;?[ \t]*$   ← anchored
PY  (services/tools.py:29)  …tools/([a-z0-9-]+)\.scad[ \t]*>                    ← not anchored
```

So `use <tools/screw.scad>; cube(10);` is a reference to Python but not to
TypeScript. Consequence: `_still_referenced` keeps the on-disk copy, while
`extractToolFiles` never stages the tool into the worker's VFS — that render
fails to resolve a `use` line the user can plainly see. Low impact, and it errs
in the safe direction (a stale copy, not a deleted one), but it is a live bug.

Resolution: **the permissive form is canonical** — OpenSCAD accepts trailing
code after `use <…>;` on the same line, so the anchored TS version is the wrong
one. Drop `[ \t]*;?[ \t]*$` from the TS regex, and add
`backend/tests/fixtures/tool_use_cases.json` — `{source, expected[]}` pairs read
by a pytest case and a Vitest case — covering: bare `use`, `include`, leading
tabs, spaces inside `<>`, missing semicolon, **trailing code on the line**,
uppercase slug (must not match), nested path (must not match), `//`-commented
line (must not match), and a line inside a `/* */` block (currently matches —
document as a known limitation rather than fixing).

One follow-on: `findLineRange` removes a tool by exact-line match, so a
hand-written `use <tools/x.scad>; cube(10);` will show as added in the toolbar
but clicking it off won't rewrite the line. Acceptable for v1 — the highlight is
at least honest that the reference exists. Note it in the code.

**R10 — concurrent edits are last-write-wins.** Pre-existing (two browser tabs
could already do this), but the split makes it easier to reach: the Editor tab
and a project's Edit modal can now hold the same file open at once. There is no
`ETag` or lock on `PUT /models/sources/content`.

Mitigation, cheap enough for v1: on Save, re-read the file and compare against
the content the buffer was seeded with; if it changed underneath, warn and make
the user confirm the overwrite. Not a real locking scheme, and not worth building
one for a single-user app — but it turns silent data loss into a question.
Verified by #20.

**R7 — losing `git blame` on ~2 700 lines** is fully handled by Phase 1's
`git filter-repo`. Recorded here only so the reason that phase exists is not
lost; it is not a residual risk.

### 4.3 Accepted risks

| ID | Risk | Why it is accepted |
|---|---|---|
| **R2** | Design tokens fork between the repos | D3. A palette change in one repo will not reach the other. Confined to `tokens.css` + `tailwind.tokens.cjs`, each carrying a header comment naming its counterpart, so reconciling is a two-file `diff`. Revisit only if drift actually bites |
| **R11** | The editor container has unauthenticated rw access to the whole library | Identical to Forge Ledger's own posture (LAN, single user, no login). Held at *one* exposed surface by **not publishing the editor's port** — it is reachable only through the host's `/editor/` proxy. If the R5 escape hatch (publish the port, absolute URL in `health.editor.path`) is ever used, this stops being true; do not take that route without deciding to |
| **R13** | Tool management disappears when the editor is off | Intended. `_shared/tools/` is plain `.scad` + `.png` files on disk — editable by hand, and unaffected by the editor's absence. Settings simply loses the section |
| **R14** | Both containers could seed default tools | Only if an old host image runs alongside the editor. `ensure_defaults()` never overwrites an existing file, so the outcome is identical either way. Harmless |

---

## Part 5 — Effort estimate

| Phase | Work | Estimate |
|---|---|---|
| 0 | Baseline + acceptance screenshots | 1 h |
| 1 | Repo split with history (`git filter-repo`) | 2 h |
| 2 | Standalone editor: both host adapters (D2), tools service rewrite, base path (R1), token extraction (D3), traversal guards (R12), regex fixtures (R3), Dockerfile | 2 days |
| 3 | Host proxy, availability probe, contract + marker checks (D4, R4, R9) | 4 h |
| 4 | Host UI: conditional nav, iframe page, modal, postMessage invalidation | 4 h |
| 5 | Host backend deletion | 1 h |
| 6 | Compose wiring, both files | 1 h |
| 7 | Verification — 20 checks against a library **copy** | 4 h |
| 8 | Docs + CI in both repos | 3 h |

**≈ 4 focused days.** Phase 2 still dominates and still carries the uncertainty,
but the two things that were genuinely unknown when this plan was written — how
the wasm resolves its own path, and whether the two regexes agreed — have both
been settled by inspection rather than left to be discovered mid-migration.
