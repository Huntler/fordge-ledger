import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import CodeMirror from "@uiw/react-codemirror";
import { cpp } from "@codemirror/lang-cpp";
import { StlViewer } from "react-stl-viewer";
import { api } from "../api";
import { useUi } from "../store";
import { ScadRenderer } from "../lib/scadRenderer";

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
 * WebAssembly in a worker (see ../workers/openscad.worker.ts) — nothing here
 * touches the network beyond Save/Export, so editing and previewing work
 * offline.
 *
 * `relPath` null means "unsaved new source"; Save then creates it via the
 * upload endpoint. Once it has a path, Save overwrites that file in place,
 * and Export compiles the current code straight to an `.stl` beside it.
 *
 * Layout-agnostic on purpose: fills whatever `className` gives it, so it
 * works both full-screen (ScadEditor's modal) and embedded in a page column
 * (the Editor page's explorer layout).
 */
export function ScadWorkspace({
  projectId,
  relPath,
  initialCode,
  onSaved,
  onClose,
  className = "",
}: {
  projectId: string;
  relPath: string | null;
  initialCode: string;
  onSaved: (relPath: string) => void;
  /** Shows a Close button when provided — omit for an embedded, page-level workspace. */
  onClose?: () => void;
  className?: string;
}) {
  const notify = useUi((s) => s.notify);
  const queryClient = useQueryClient();

  const [code, setCode] = useState(initialCode);
  const [filename, setFilename] = useState(relPath?.split("/").pop() ?? "new-part.scad");
  const [stlUrl, setStlUrl] = useState<string | null>(null);
  const [rendering, setRendering] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);

  const rendererRef = useRef<ScadRenderer | null>(null);
  const stlUrlRef = useRef<string | null>(null);
  const lastStlRef = useRef<string | null>(null);
  const renderIdRef = useRef(0);

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
      const stl = await rendererRef.current!.render(code);
      if (renderIdRef.current !== id) return;
      lastStlRef.current = stl;
      const url = URL.createObjectURL(new Blob([stl], { type: "model/stl" }));
      if (stlUrlRef.current) URL.revokeObjectURL(stlUrlRef.current);
      stlUrlRef.current = url;
      setStlUrl(url);
      setRenderError(null);
    } catch (err) {
      if (renderIdRef.current !== id) return;
      const message = err instanceof Error ? err.message : String(err);
      setRenderError(explainRenderError(message));
    } finally {
      if (renderIdRef.current === id) setRendering(false);
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

      <div className="flex-1 grid grid-cols-2 min-h-0">
        <div className="min-h-0 overflow-auto border-r border-ink-600">
          <CodeMirror
            value={code}
            height="100%"
            theme="dark"
            extensions={[cpp()]}
            onChange={(value) => setCode(value)}
          />
        </div>
        <div className="relative min-h-0 bg-ink-900">
          {stlUrl ? (
            <StlViewer
              url={stlUrl}
              orbitControls
              shadows
              style={{ width: "100%", height: "100%" }}
            />
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
}
