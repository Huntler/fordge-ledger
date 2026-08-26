# Phase 4 — Host-side UI

Repo: this one (`fordge-ledger/frontend`).

## 4a — `App.tsx`: conditional nav + route

`NAV` is now built from `health?.editor.available` (the probe result from
Phase 3, not `FORGE_EDITOR_URL`'s presence). The `/editor` route renders a
new `pages/EditorFrame.tsx` — a ~25-line full-bleed `<iframe src={health.editor.path}>`
that redirects to `/library` if the editor isn't available (a bookmarked
`/editor` must not render a blank frame). The existing `isEditor` full-width
`<main>` rule stayed — it still applies to the same route.

## 4b — `pages/Project.tsx`: Sources tab

- `SourcesTab` now also queries `["health"]`. `onEditScad` is only passed to
  `SourceList` (and the "+ New .scad" button only rendered) when
  `health?.editor.available` — without the editor, Sources still lists,
  downloads and deletes `.scad` files, it just can't open one (exactly the
  plan's "there is nothing to open" framing).
- The old lazy `<ScadEditor>` modal is replaced by a new `EditorModal`
  (defined right in `Project.tsx`, ~35 lines) — a modal iframe at
  `${health.editor.path}?project={id}&file={relPath}&embed=1` (`file`
  omitted → new file). **Simplification this enabled:** the host no longer
  pre-fetches the file's text via `readTextFile` before opening the modal —
  the embedded editor fetches it itself (see forge-scad-editor's deep-link
  effect in `pages/Editor.tsx`, built in Phase 2). `openScad` shrank from an
  async `readTextFile` call to a plain state set.
- `lib/scadTemplate.ts`'s `NEW_SCAD_TEMPLATE` import is gone — deleted along
  with the file (see below); a new file is seeded by the editor's own
  template now, not the host's.

## 4c — Cache coherence (`useEditorSync`)

New `hooks/useEditorSync.ts`: one global `window.addEventListener("message", …)`
in `App.tsx`, verifying `event.origin === window.location.origin` before
trusting `{type: "scad-editor:changed", projectId, relPath}` (posted by the
editor on save/export/delete — see forge-scad-editor's `notifyHostChanged`,
written in Phase 2), then invalidating `["sources", projectId]` and
`["project", projectId]`. One listener, not one per embedding point — the
message carries its own `projectId`, so it doesn't matter whether it came
from the full-page `EditorFrame` or the Sources-tab modal.

## 4d — `pages/Settings.tsx`

`ToolsSettings` import and render removed. In its place, a small
conditional card (only rendered when `health.editor.available`) linking to
`${health.editor.path}settings` — "tool management now lives at
`/editor/settings`," per the plan's optional suggestion.

## 4e — `api.ts`

`Tool` interface and the six tools methods deleted. `Health` gained
`editor: EditorAvailability` (`available`, `reason`, and the optional
`path`/`version`/`host_contract` fields the probe's positive-path response
spreads in — see `state.py`'s `editor_status()`).

## 4f — Delete the editor-owned frontend files, then shrink `package.json`

**Not explicitly itemized as a phase step in the plan's checklist, but
required by it:** §4f says removing the 11 editor-only packages and running
`npm install` is part of this phase, and that's only possible once nothing
in the host still imports `three`, `@codemirror/*`, `@uiw/react-codemirror`,
or `@lezer/*` — which meant actually deleting the editor-owned `.tsx`/`.ts`
files themselves, not just their usages in `App.tsx`/`Project.tsx`/`Settings.tsx`.
Confirmed necessary empirically: after 4a–4e, `npm run typecheck` failed with
~25 errors, all in the now-orphaned files themselves (`ScadWorkspace.tsx`,
`ScadToolbar.tsx`, `ToolForm.tsx`, `ToolsSettings.tsx` — each still importing
`Tool`/`tools()`/`saveTool()`/etc. from `api.ts`, which no longer exports
them). `noUnusedLocals` doesn't catch dead files, only dead identifiers
*within* a file that's still part of the build — these files needed to
leave the build entirely.

`git rm`'d (matching the plan's §1.2 "owned exclusively by the editor" list
exactly): `pages/Editor.tsx`, `components/{ScadWorkspace,ScadEditor,ScadToolbar,ScadObjectList,StlPreview,FileExplorer,ToolForm,ToolsSettings}.tsx`,
`lib/scad{Objects,Renderer,Thumbnail,Template}.ts`, `workers/openscad.worker.ts`,
`lang-openscad/` (whole directory, 10 files), `scripts/fetch-openscad-wasm.mjs`.
**Not** deleted: `components/DropZone.tsx` and `components/ui.tsx` — both
plan-designated *shared* infrastructure the editor borrowed rather than
owned (`DropZone` is still used by `Project.tsx`'s own CAD/image uploads;
`ui.tsx` keeps all 13 exports, since `Modal`/`ConfirmDialog`/`Spinner`/`EmptyState`
are still used elsewhere in the host — only `Tooltip` and `Switch` are now
unreferenced, but the plan doesn't ask for them to be pruned and an unused
*export* isn't a build error, so they stay as generically-useful primitives
rather than being deleted speculatively).

`package.json`: removed `predev`/`prebuild` (wasm fetch) and `grammar:build`
scripts, and all 11 editor-only dependencies (`@codemirror/*` ×4, `@lezer/*`
×4, `@uiw/react-codemirror`, `three`, plus dev deps `@lezer/generator` and
`@types/three`). `.gitignore`'s `frontend/public/openscad/` entry removed
(nothing fetches into it anymore). `npm install` → **28 packages removed**,
`package-lock.json` shrunk accordingly.

`components/ui.tsx`'s stale comment referencing `ScadToolbar` (plan §1.5)
reworded to point at forge-scad-editor's own copy instead.

## Verified

- `npm run typecheck` — clean, `noUnusedLocals`/`noUnusedParameters` on, no
  dangling imports.
- `npm run build`:

  | | Before (Phase 0 baseline) | After |
  |---|---|---|
  | Main JS chunk | 693.98 KB (220.91 KB gzip) | **285.84 KB (85.91 KB gzip)** |
  | Lazy `ScadWorkspace` chunk | 567.04 KB (148.83 KB gzip) | *(gone)* |
  | Chunk-size warning | yes (`>500kB`) | no |

  This is Phase 7 verification check #14, satisfied here rather than
  re-measured later — a 59% cut to the chunk that always loads, and the
  ~567 KB lazy chunk (three.js + CodeMirror + Lezer) is gone entirely, not
  just deferred.
- Backend suite unaffected by this phase (no backend files touched here):
  still 177 passed, confirmed by re-running after these changes.

Next: [Phase 5 — delete editor code from the host backend](05-backend-deletion.md)
(only the backend half remains — the frontend half of "delete the editor's
own code from the host" happened above, in 4f, since it was a hard
prerequisite for 4a–4e to typecheck at all).
