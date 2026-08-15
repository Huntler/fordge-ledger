import { lazy, Suspense, useRef, useState } from "react";
import { FileExplorer, type ScadSelection } from "../components/FileExplorer";
import { ScadToolbar } from "../components/ScadToolbar";
import type { ScadWorkspaceHandle } from "../components/ScadWorkspace";
import { EmptyState, Spinner } from "../components/ui";

// CodeMirror + three.js + the ~14MB openscad-wasm worker only load once a
// file is actually opened — landing on this page costs nothing extra.
const ScadWorkspace = lazy(() =>
  import("../components/ScadWorkspace").then((m) => ({ default: m.ScadWorkspace })),
);

/**
 * Library-wide SCAD editor: every project's `.scad` sources in one explorer,
 * so switching between parts across projects doesn't mean bouncing through
 * each project's own Sources tab.
 */
export default function EditorPage() {
  const [selection, setSelection] = useState<ScadSelection | null>(null);
  const [addedTools, setAddedTools] = useState<Set<string>>(new Set());
  const workspaceRef = useRef<ScadWorkspaceHandle>(null);

  return (
    <div className="flex gap-4 h-[calc(100vh-8.5rem)]">
      <FileExplorer
        selected={selection}
        onSelect={setSelection}
        onFileRemoved={(projectId, relPath) =>
          setSelection((s) =>
            s && s.projectId === projectId && s.relPath === relPath ? null : s,
          )
        }
      />

      <div className="flex-1 min-w-0 flex flex-col gap-4">
        {selection ? (
          <>
            <Suspense
              fallback={
                <div className="card flex-1 min-h-0 flex items-center justify-center">
                  <Spinner label="Loading OpenSCAD…" />
                </div>
              }
            >
              <ScadWorkspace
                // Force a remount on file switch — internal editor state seeds
                // from initialCode once at mount and never re-syncs from props.
                key={`${selection.projectId}:${selection.relPath ?? "new"}`}
                ref={workspaceRef}
                projectId={selection.projectId}
                relPath={selection.relPath}
                initialCode={selection.code}
                onSaved={(relPath) => setSelection((s) => (s ? { ...s, relPath } : s))}
                onToolsChanged={(names) => setAddedTools(new Set(names))}
                className="flex-1 min-h-0"
              />
            </Suspense>
            <ScadToolbar
              className="shrink-0"
              addedTools={addedTools}
              onToggle={(name) => workspaceRef.current?.toggleTool(name)}
            />
          </>
        ) : (
          <div className="card h-full flex items-center justify-center">
            <EmptyState
              title="No file open"
              hint="Pick a .scad file on the left, or add a new one with the + next to a project."
            />
          </div>
        )}
      </div>
    </div>
  );
}
