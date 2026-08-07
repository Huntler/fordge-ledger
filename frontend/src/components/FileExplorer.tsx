import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, readTextFile, type ProjectSummary } from "../api";
import { ConfirmDialog, Spinner } from "./ui";
import { useUi } from "../store";
import { NEW_SCAD_TEMPLATE } from "../lib/scadTemplate";

export interface ScadSelection {
  projectId: string;
  relPath: string | null;
  code: string;
}

const MODEL_SOURCES_PREFIX = "models/sources/";

/**
 * Left-hand file tree for the Editor page: every project as a folder,
 * expanded lazily to list its `.scad` sources — the same "the folder is the
 * product" data the Sources tab shows, just browsable across the whole
 * library instead of one project at a time.
 */
export function FileExplorer({
  selected,
  onSelect,
  onFileRemoved,
}: {
  selected: ScadSelection | null;
  onSelect: (selection: ScadSelection) => void;
  onFileRemoved: (projectId: string, relPath: string) => void;
}) {
  const { data: projects, isLoading } = useQuery({
    queryKey: ["projects", {}],
    queryFn: () => api.projects({}),
  });

  return (
    <aside className="w-72 shrink-0 card p-2 overflow-y-auto">
      <h2 className="px-2 py-1.5 text-xs font-medium uppercase tracking-wide text-slate-500">
        Projects
      </h2>
      {isLoading ? (
        <Spinner />
      ) : !projects || projects.length === 0 ? (
        <p className="px-2 py-1 text-sm text-slate-500">No projects yet.</p>
      ) : (
        <ul className="space-y-0.5">
          {projects.map((project) => (
            <ProjectNode
              key={project.id}
              project={project}
              selected={selected}
              onSelect={onSelect}
              onFileRemoved={onFileRemoved}
            />
          ))}
        </ul>
      )}
    </aside>
  );
}

function ProjectNode({
  project,
  selected,
  onSelect,
  onFileRemoved,
}: {
  project: ProjectSummary;
  selected: ScadSelection | null;
  onSelect: (selection: ScadSelection) => void;
  onFileRemoved: (projectId: string, relPath: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const notify = useUi((s) => s.notify);
  const queryClient = useQueryClient();

  const { data: sources, isLoading } = useQuery({
    queryKey: ["sources", project.id],
    queryFn: () => api.sources(project.id),
    enabled: open,
  });

  const scadFiles = (sources?.models ?? []).filter((f) =>
    f.rel_path.toLowerCase().endsWith(".scad"),
  );

  const openFile = async (relPath: string) => {
    try {
      onSelect({ projectId: project.id, relPath, code: await readTextFile(project.id, relPath) });
    } catch (err) {
      notify(err instanceof Error ? err.message : String(err), "error");
    }
  };

  const remove = useMutation({
    mutationFn: (relPath: string) => api.deleteModelSource(project.id, relPath),
    onSuccess: (_, relPath) => {
      queryClient.invalidateQueries({ queryKey: ["sources", project.id] });
      queryClient.invalidateQueries({ queryKey: ["project", project.id] });
      notify(`Deleted ${relPath.slice(MODEL_SOURCES_PREFIX.length)}`, "success");
      onFileRemoved(project.id, relPath);
      setPendingDelete(null);
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  return (
    <li>
      <div className="flex items-center gap-1 rounded hover:bg-ink-700 group">
        <button
          type="button"
          className="flex items-center gap-1.5 flex-1 min-w-0 text-left text-sm px-2 py-1.5"
          onClick={() => setOpen((o) => !o)}
        >
          <span className="text-slate-500 w-3 shrink-0 text-xs">{open ? "▾" : "▸"}</span>
          <span className="truncate">{project.title}</span>
        </button>
        <button
          type="button"
          className="btn btn-ghost text-xs px-2 py-0 opacity-0 group-hover:opacity-100 shrink-0"
          title="New .scad in this project"
          onClick={() => onSelect({ projectId: project.id, relPath: null, code: NEW_SCAD_TEMPLATE })}
        >
          +
        </button>
      </div>

      {open && (
        <ul className="pl-6 pb-1">
          {isLoading ? (
            <li className="text-xs text-slate-600 px-2 py-1">Loading…</li>
          ) : scadFiles.length === 0 ? (
            <li className="text-xs text-slate-600 px-2 py-1">No .scad files</li>
          ) : (
            scadFiles.map((file) => {
              const isSelected =
                selected?.projectId === project.id && selected?.relPath === file.rel_path;
              return (
                <li key={file.rel_path} className="flex items-center gap-1 group/file">
                  <button
                    type="button"
                    className={`flex-1 min-w-0 text-left text-xs font-mono px-2 py-1 rounded truncate ${
                      isSelected ? "bg-accent/20 text-accent" : "text-slate-400 hover:bg-ink-700"
                    }`}
                    onClick={() => openFile(file.rel_path)}
                  >
                    {file.rel_path.slice(MODEL_SOURCES_PREFIX.length)}
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost text-xs px-1 py-0 opacity-0 group-hover/file:opacity-100 hover:text-rose-400 shrink-0"
                    title="Delete"
                    onClick={() => setPendingDelete(file.rel_path)}
                  >
                    ✕
                  </button>
                </li>
              );
            })
          )}
        </ul>
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete file"
        message={`Delete "${pendingDelete?.slice(MODEL_SOURCES_PREFIX.length)}"? This removes it from disk — there is no undo.`}
        onConfirm={() => pendingDelete && remove.mutate(pendingDelete)}
        onCancel={() => setPendingDelete(null)}
      />
    </li>
  );
}
