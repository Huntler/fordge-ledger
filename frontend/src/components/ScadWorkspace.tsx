import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import CodeMirror, { type ReactCodeMirrorRef } from "@uiw/react-codemirror";
import { autocompletion, closeBrackets } from "@codemirror/autocomplete";
import { lintGutter } from "@codemirror/lint";
import { StlPreview } from "./StlPreview";
import { ScadObjectList } from "./ScadObjectList";
import { openscad, toolSourcesFacet } from "../lang-openscad";
import { api, readTextFile, type ProjectSources, type Tool } from "../api";
import { useUi } from "../store";
import { ScadRenderer, type RenderQuality } from "../lib/scadRenderer";
import { computeScadObjects, isolateScadObject, toggleScadObject, type ScadObject } from "../lib/scadObjects";
import { renderStlThumbnail } from "../lib/scadThumbnail";

// Matches a `use <tools/slug.scad>;` or `include <tools/slug.scad>;` line —
// how a referenced tool (see ScadToolbar) gets resolved at render time. Not
// a real OpenSCAD parser, just enough to find what to write into the
// worker's virtual FS; matches the app's existing "good enough" lightweight
// parsing elsewhere (e.g. the hand-rolled Markdown renderer in ui.tsx).
const TOOL_USE_RE = /^[ \t]*(?:use|include)[ \t]*<[ \t]*tools\/([a-z0-9-]+)\.scad[ \t]*>[ \t]*;?[ \t]*$/gm;

function extractReferencedToolNames(code: string): Set<string> {
  const names = new Set<string>();
  for (const match of code.matchAll(TOOL_USE_RE)) names.add(match[1]);
  return names;
}

// `sources` is every .scad text that ends up in the render's virtual FS —
// the open buffer plus whatever sibling project files loadProjectFiles
// pulled in — not just the open buffer. A sibling can `use <tools/...>;` on
// its own account (e.g. a part file that wraps a tool in its own module),
// and that reference is invisible if only the top-level buffer gets
// scanned: the sibling would get staged into the VFS, but the tool it
// itself depends on wouldn't, so OpenSCAD fails resolving *that* file's own
// `use` line even though the file that actually failed to render never
// mentioned tools/ at all. One flat scan rather than a real transitive
// resolve — good enough as long as a tool's own body doesn't itself `use`
// another tool, which none of the shipped ones do.
function extractToolFiles(sources: Iterable<string>, tools: Tool[]): Record<string, string> {
  const files: Record<string, string> = {};
  for (const code of sources) {
    for (const name of extractReferencedToolNames(code)) {
      if (files[`tools/${name}.scad`]) continue;
      const tool = tools.find((t) => t.name === name);
      if (tool) files[`tools/${name}.scad`] = tool.body;
    }
  }
  return files;
}

const MODEL_SOURCES_PREFIX = "models/sources/";

// Every other .scad in the project, fetched fresh and keyed by its path
// relative to models/sources/ — the same scheme a copied-in tool uses
// (models/sources/tools/foo.scad -> tools/foo.scad) — so a `use <bar.scad>;`
// or `import <sub/bar.scad>;` line resolves against the rest of the project,
// not just tool snippets. Excludes the file currently open (its live buffer,
// not the possibly-stale on-disk copy, is what's already at /input.scad) and
// anything under tools/ (the addTool/removeTool mutations already stage
// those, and they're excluded from the sources listing itself). Reads
// current disk content on every call rather than caching — cheap against a
// local backend, and it means a render always sees the latest saved sibling
// state instead of what was true when the panel opened.
async function loadProjectFiles(
  projectId: string,
  sources: ProjectSources | undefined,
  currentRelPath: string | null,
): Promise<Record<string, string>> {
  const siblings = (sources?.models ?? []).filter(
    (f) => f.rel_path.toLowerCase().endsWith(".scad") && f.rel_path !== currentRelPath,
  );
  const entries = await Promise.all(
    siblings.map(async (f) => {
      const vfsPath = f.rel_path.startsWith(MODEL_SOURCES_PREFIX)
        ? f.rel_path.slice(MODEL_SOURCES_PREFIX.length)
        : f.rel_path;
      try {
        return [vfsPath, await readTextFile(projectId, f.rel_path)] as const;
      } catch {
        // A sibling that vanished or failed to load between listing and
        // fetch shouldn't sink the whole render — any `use`/`include` that
        // needed it fails on its own, same as a genuinely missing file.
        return null;
      }
    }),
  );
  return Object.fromEntries(entries.filter((e): e is readonly [string, string] => e !== null));
}

// Where a new `use <tools/...>;` line should land: after any leading block
// of use/include lines (so repeated tool references group together in
// click order), before the first line of real code.
function leadingUseBlockEnd(code: string): number {
  const USE_LINE = /^[ \t]*(?:use|include)[ \t]*<[^>]+>[ \t]*;?[ \t]*$/;
  let offset = 0;
  for (const line of code.split("\n")) {
    if (!USE_LINE.test(line)) break;
    offset += line.length + 1;
  }
  return offset;
}

/** The `{from, to}` span of an exact line (including its trailing newline,
 * clamped to the document length if it's the last line and has none) — used
 * to remove a `use <tools/...>;` line precisely when a tool is toggled off. */
function findLineRange(code: string, line: string): { from: number; to: number } | null {
  let offset = 0;
  for (const current of code.split("\n")) {
    if (current === line) {
      return { from: offset, to: Math.min(offset + current.length + 1, code.length) };
    }
    offset += current.length + 1;
  }
  return null;
}

// A native WASM crash (rather than OpenSCAD reporting a real compile error)
// surfaces as a bare stringified pointer instead of a message, e.g.
// "1124712". Each render gets its own fresh module instance specifically to
// avoid the one reliable cause of this found so far (see openscad.worker.ts),
// but very large or degenerate geometry could still exhaust the sandbox.
// See README.md § "The in-browser SCAD editor".
function explainRenderError(message: string): string {
  if (/^\d+$/.test(message.trim())) {
    return (
      `${message} — OpenSCAD's WASM sandbox crashed natively instead of ` +
      "reporting a normal error, most likely from very large or degenerate " +
      "geometry. Simplify the model and try again."
    );
  }
  return message;
}

/**
 * SCAD source editor with a live 3D preview. OpenSCAD itself runs as
 * WebAssembly in a worker (see ../workers/openscad.worker.ts) — actual
 * compilation never leaves the browser. Rendering does still hit the network
 * against the local backend, to pull in the rest of the project (see
 * loadProjectFiles) and any referenced tools, so it isn't offline end to end
 * — only the OpenSCAD run itself is.
 *
 * `relPath` null means "unsaved new source"; Save then creates it via the
 * upload endpoint. Once it has a path, Save overwrites that file in place,
 * and Export compiles the current code straight to an `.stl` beside it.
 *
 * Layout-agnostic on purpose: fills whatever `className` gives it, so it
 * works both full-screen (ScadEditor's modal) and embedded in a page column
 * (the Editor page's explorer layout).
 */
export interface ScadWorkspaceHandle {
  /** Toggles a tool's reference: adds `use <tools/<toolName>.scad>;` (after
   * any existing leading use/include lines) and copies the tool's .scad
   * into this project's models/sources/tools/ if it isn't referenced yet,
   * or removes both the line and the copy if it already is — how the
   * Editor page's tools toolbar makes a tool's modules/functions callable
   * without inlining its body. */
  toggleTool: (toolName: string) => void;
}

export const ScadWorkspace = forwardRef<
  ScadWorkspaceHandle,
  {
    projectId: string;
    relPath: string | null;
    initialCode: string;
    onSaved: (relPath: string) => void;
    /** Shows a Close button when provided — omit for an embedded, page-level workspace. */
    onClose?: () => void;
    /** Fired whenever the set of tools referenced by the buffer changes
     * (toggled via the handle, or hand-edited) — lets the toolbar highlight
     * which tools are currently added. */
    onToolsChanged?: (toolNames: string[]) => void;
    className?: string;
  }
>(function ScadWorkspace(
  { projectId, relPath, initialCode, onSaved, onClose, onToolsChanged, className = "" },
  ref,
) {
  const notify = useUi((s) => s.notify);
  const queryClient = useQueryClient();

  const [code, setCode] = useState(initialCode);
  const [objects, setObjects] = useState<ScadObject[]>(() => computeScadObjects(initialCode));
  const [thumbnails, setThumbnails] = useState<Record<string, string>>({});
  const [filename, setFilename] = useState(relPath?.split("/").pop() ?? "new-part.scad");
  const [stlUrl, setStlUrl] = useState<string | null>(null);
  const [rendering, setRendering] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [quality, setQuality] = useState<RenderQuality>("medium");

  const rendererRef = useRef<ScadRenderer | null>(null);
  const stlUrlRef = useRef<string | null>(null);
  const lastStlRef = useRef<string | null>(null);
  const renderIdRef = useRef(0);
  const cmRef = useRef<ReactCodeMirrorRef>(null);
  // Thumbnail cache survives across renders (keyed by an object's own
  // statement text — see ScadObject.text), so re-rendering after an
  // unrelated edit doesn't redo the still-unchanged objects' thumbnails.
  // A ref rather than state: it's read-modify-write from an async loop and
  // must never trigger a render itself — setThumbnails (below) does that.
  const thumbnailCacheRef = useRef<Map<string, string>>(new Map());
  const { data: tools } = useQuery({ queryKey: ["tools"], queryFn: api.tools });
  // Shares the ["sources", projectId] cache with Project.tsx's file browser
  // — already invalidated by this file's own save mutation below, so a
  // sibling saved from here shows up for the next render without extra
  // wiring. Only the listing (names/paths) is cached; loadProjectFiles
  // fetches each sibling's actual content fresh per render — see there.
  const { data: sources } = useQuery({ queryKey: ["sources", projectId], queryFn: () => api.sources(projectId) });

  // The physical copy-in/remove side of toggling a tool (see the handle
  // below) — a real file under models/sources/tools/, not just the
  // worker's virtual FS injection at render time, so a saved source still
  // resolves its `use <tools/...>;` lines outside this app. Optimistic:
  // the buffer edit happens immediately regardless of how these settle,
  // same as every other mutation in this file (Save, Export).
  const addTool = useMutation({
    mutationFn: (toolName: string) => api.addToolToProject(projectId, toolName),
    onError: (error: Error) => notify(error.message, "error"),
  });
  const removeTool = useMutation({
    mutationFn: (toolName: string) => api.removeToolFromProject(projectId, toolName),
    onError: (error: Error) => notify(error.message, "error"),
  });

  useImperativeHandle(ref, () => ({
    toggleTool(toolName: string) {
      const line = `use <tools/${toolName}.scad>;`;
      const view = cmRef.current?.view;
      const currentText = view ? view.state.doc.toString() : code;
      const referenced = currentText.split("\n").includes(line);

      if (referenced) {
        const range = findLineRange(currentText, line);
        if (range && view) {
          view.dispatch({ changes: { from: range.from, to: range.to, insert: "" } });
        } else if (range) {
          setCode(currentText.slice(0, range.from) + currentText.slice(range.to));
        }
        removeTool.mutate(toolName);
      } else {
        const insertAt = leadingUseBlockEnd(currentText);
        if (view) {
          view.dispatch({ changes: { from: insertAt, insert: `${line}\n` } });
          view.focus();
        } else {
          // View not mounted yet (shouldn't normally happen) — edit the
          // string directly rather than silently dropping the reference.
          setCode(currentText.slice(0, insertAt) + line + "\n" + currentText.slice(insertAt));
        }
        addTool.mutate(toolName);
      }
    },
  }));

  // Keeps the toolbar's "which tools are added" highlight in sync with the
  // buffer even if a use/include line is added or removed by hand rather
  // than by clicking a tool.
  useEffect(() => {
    onToolsChanged?.(Array.from(extractReferencedToolNames(code)));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- onToolsChanged
    // intentionally excluded: re-firing only on code changes, not on every
    // parent re-render that happens to pass a new closure.
  }, [code]);

  // Keeps the object stack (see ScadObjectList) in sync with the buffer,
  // the same way the effect above keeps the tools toolbar in sync — including
  // when a toggle below edits the buffer itself, so a click's effect on the
  // list is driven by the resulting parse rather than applied optimistically.
  useEffect(() => {
    setObjects(computeScadObjects(code));
  }, [code]);

  // Comments a top-level object's statement out of the buffer (or restores
  // it) — see toggleScadObject. Mirrors the handle's toggleTool below:
  // prefer editing the live CodeMirror view (so undo/redo see one coherent
  // edit) and fall back to raw string surgery if the view isn't mounted yet.
  const toggleObject = (object: ScadObject) => {
    const view = cmRef.current?.view;
    const currentText = view ? view.state.doc.toString() : code;
    const edit = toggleScadObject(currentText, object);
    if (!edit) {
      notify('Can\'t toggle — this statement contains "*/", which would break the wrapping comment.', "error");
      return;
    }
    if (view) {
      view.dispatch({ changes: { from: edit.from, to: edit.to, insert: edit.insert } });
    } else {
      setCode(currentText.slice(0, edit.from) + edit.insert + currentText.slice(edit.to));
    }
  };

  useEffect(() => {
    const renderer = new ScadRenderer();
    rendererRef.current = renderer;
    return () => {
      renderer.terminate();
      if (stlUrlRef.current) URL.revokeObjectURL(stlUrlRef.current);
    };
  }, []);

  // Rendering is manual (the Render button) rather than on every keystroke —
  // each render pays full WASM instantiation (see openscad.worker.ts), which
  // is too slow to fire on every edit. A stale in-flight render must still
  // never clobber a newer one's result if Render is clicked again quickly.
  const renderNow = async () => {
    const id = ++renderIdRef.current;
    setRendering(true);
    try {
      const projectFiles = await loadProjectFiles(projectId, sources, relPath);
      if (renderIdRef.current !== id) return;
      const files = {
        ...extractToolFiles([code, ...Object.values(projectFiles)], tools ?? []),
        ...projectFiles,
      };
      const stl = await rendererRef.current!.render(code, quality, files);
      if (renderIdRef.current !== id) return;
      lastStlRef.current = stl;
      const url = URL.createObjectURL(new Blob([stl], { type: "model/stl" }));
      if (stlUrlRef.current) URL.revokeObjectURL(stlUrlRef.current);
      stlUrlRef.current = url;
      setStlUrl(url);
      setRenderError(null);
      generateThumbnails(id, code, files);
    } catch (err) {
      if (renderIdRef.current !== id) return;
      const message = err instanceof Error ? err.message : String(err);
      setRenderError(explainRenderError(message));
    } finally {
      if (renderIdRef.current === id) setRendering(false);
    }
  };

  // Renders each object's own low-quality thumbnail (see ScadObjectList),
  // one at a time — every render pays full WASM instantiation, so this
  // deliberately never runs more than one in flight alongside the main
  // render or another object's thumbnail. Cache-hit objects (unchanged
  // since the last successful render) cost nothing beyond the lookup.
  // Fire-and-forget from renderNow: a slow or failed thumbnail must never
  // hold up the main preview, which has already landed by the time this
  // starts.
  const generateThumbnails = async (id: number, sourceCode: string, files: Record<string, string>) => {
    const renderer = rendererRef.current;
    if (!renderer) return;
    const currentObjects = computeScadObjects(sourceCode);
    for (const object of currentObjects) {
      if (renderIdRef.current !== id) return; // a newer render superseded this pass
      const key = object.text.trim();
      const cached = thumbnailCacheRef.current.get(key);
      if (cached) {
        setThumbnails((prev) => (prev[key] === cached ? prev : { ...prev, [key]: cached }));
        continue;
      }
      try {
        const isolated = isolateScadObject(sourceCode, object, currentObjects);
        const stl = await renderer.render(isolated, "low", files);
        if (renderIdRef.current !== id) return;
        const dataUrl = renderStlThumbnail(stl);
        thumbnailCacheRef.current.set(key, dataUrl);
        setThumbnails((prev) => ({ ...prev, [key]: dataUrl }));
      } catch {
        // Best-effort — an object that fails to render in isolation (rare:
        // e.g. it actually depends on a sibling object's geometry) just
        // keeps showing the placeholder in the list; the main preview
        // already rendered fine.
      }
    }
  };

  const save = useMutation({
    mutationFn: async () => {
      if (relPath) {
        await api.writeModelSource(projectId, relPath, code);
        return relPath;
      }
      const name = filename.trim().toLowerCase().endsWith(".scad")
        ? filename.trim()
        : `${filename.trim()}.scad`;
      const file = new File([code], name, { type: "text/plain" });
      const result = await api.uploadModelSource(projectId, file);
      return result.rel_path;
    },
    onSuccess: (savedPath) => {
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      queryClient.invalidateQueries({ queryKey: ["sources", projectId] });
      notify(`Saved ${savedPath}`, "success");
      onSaved(savedPath);
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  // Export needs a saved path to name the .stl after, and a successful
  // render to export — an unsaved file or a crashed one has nothing to give.
  const exportStl = useMutation({
    mutationFn: async () => {
      if (!relPath) throw new Error("Save the source before exporting");
      if (!lastStlRef.current) throw new Error("Nothing rendered yet to export");
      return api.exportModelStl(projectId, relPath, lastStlRef.current);
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      queryClient.invalidateQueries({ queryKey: ["sources", projectId] });
      notify(`Exported ${result.rel_path}`, "success");
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  return (
    <div className={`card flex flex-col overflow-hidden ${className}`}>
      <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-ink-600 shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <h2 className="font-medium truncate">
            {relPath ? relPath.slice("models/sources/".length) : "New SCAD source"}
          </h2>
          {!relPath && (
            <input
              className="input text-xs w-40"
              value={filename}
              onChange={(e) => setFilename(e.target.value)}
            />
          )}
        </div>
        <div className="flex items-center gap-3">
          {onClose && (
            <button type="button" className="btn" onClick={onClose}>
              Close
            </button>
          )}
          <select
            className="btn w-auto pr-2 cursor-pointer hover:bg-ink-600"
            value={quality}
            onChange={(e) => setQuality(e.target.value as RenderQuality)}
            title="Tessellation quality (cylinder/sphere facet count)"
          >
            <option value="low">Low quality</option>
            <option value="medium">Medium quality</option>
            <option value="high">High quality</option>
          </select>
          <button type="button" className="btn" disabled={rendering} onClick={() => renderNow()}>
            {rendering ? "Rendering…" : "Render"}
          </button>
          <button
            type="button"
            className="btn"
            disabled={exportStl.isPending || !relPath || !stlUrl}
            title={!relPath ? "Save first" : `Exports as ${relPath.replace(/\.scad$/, ".stl")}`}
            onClick={() => exportStl.mutate()}
          >
            {exportStl.isPending ? "Exporting…" : "Export STL"}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      <div className="flex-1 grid grid-cols-[1fr_180px_1fr] min-h-0">
        <div className="min-h-0 overflow-auto border-r border-ink-600">
          <CodeMirror
            ref={cmRef}
            value={code}
            height="100%"
            theme="dark"
            extensions={[
              openscad(),
              lintGutter(),
              autocompletion(),
              closeBrackets(),
              // Lets a `use <tools/foo.scad>;` line autocomplete foo's own
              // modules/functions — see toolSourcesFacet in lang-openscad.
              toolSourcesFacet.of(Object.fromEntries((tools ?? []).map((t) => [t.name, t.body]))),
            ]}
            onChange={(value) => setCode(value)}
          />
        </div>
        <ScadObjectList objects={objects} thumbnails={thumbnails} onToggle={toggleObject} />
        <div className="relative min-h-0 bg-ink-900">
          {stlUrl ? (
            <StlPreview url={stlUrl} className="w-full h-full" />
          ) : (
            <div className="h-full flex items-center justify-center text-sm text-slate-500">
              {rendering ? "Rendering…" : "Click Render to preview"}
            </div>
          )}
          {renderError && (
            <div className="absolute inset-x-0 bottom-0 max-h-32 overflow-auto bg-rose-500/10 border-t border-rose-500/40 text-rose-300 text-xs font-mono p-2 whitespace-pre-wrap">
              {renderError}
            </div>
          )}
        </div>
      </div>
    </div>
  );
});
