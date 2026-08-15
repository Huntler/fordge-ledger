import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useUi } from "../store";
import { ToolForm } from "./ToolForm";
import { ConfirmDialog } from "./ui";

/**
 * Full manage-tools panel for the Settings page — modeled on Settings.tsx's
 * MarkdownLibrary (templates/snippets), plus delete, which that one doesn't
 * have. Left column lists tools with their icon/monogram; right column is
 * the same ToolForm the Editor page's toolbar "+" popup uses.
 */
export function ToolsSettings() {
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [creatingNew, setCreatingNew] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const notify = useUi((s) => s.notify);

  const { data: tools } = useQuery({ queryKey: ["tools"], queryFn: api.tools });

  const remove = useMutation({
    mutationFn: (name: string) => api.deleteTool(name),
    onSuccess: (_data, name) => {
      queryClient.invalidateQueries({ queryKey: ["tools"] });
      notify(`Deleted ${name}`, "success");
      setSelectedName((s) => (s === name ? null : s));
      setPendingDelete(null);
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  const selected = tools?.find((t) => t.name === selectedName) ?? null;

  return (
    <section className="card p-4">
      <h2 className="font-medium">Tools</h2>
      <p className="text-xs text-slate-500 mt-1 mb-3">
        Reusable OpenSCAD snippets shown in the Editor page's toolbar.
      </p>

      <div className="grid gap-4 md:grid-cols-[14rem_1fr]">
        <div className="space-y-1">
          {tools?.map((tool) => (
            <div key={tool.name} className="group flex items-center gap-1">
              <button
                type="button"
                onClick={() => {
                  setCreatingNew(false);
                  setSelectedName(tool.name);
                }}
                className={`flex-1 flex items-center gap-2 text-left px-2 py-1 rounded text-sm min-w-0 ${
                  selectedName === tool.name && !creatingNew
                    ? "bg-ink-600 text-white"
                    : "hover:bg-ink-700"
                }`}
              >
                {tool.has_icon ? (
                  <img
                    src={api.toolIconUrl(tool.name)}
                    alt=""
                    className="w-4 h-4 rounded shrink-0 object-cover"
                  />
                ) : (
                  <span className="w-4 h-4 rounded bg-ink-600 shrink-0 flex items-center justify-center text-[8px] font-medium">
                    {tool.name.slice(0, 2).toUpperCase()}
                  </span>
                )}
                <span className="truncate">{tool.name}</span>
              </button>
              <button
                type="button"
                className="btn btn-ghost text-xs px-1 py-0 opacity-0 group-hover:opacity-100 hover:text-rose-400 shrink-0"
                title="Delete"
                onClick={() => setPendingDelete(tool.name)}
              >
                ✕
              </button>
            </div>
          ))}
          <button
            type="button"
            className="btn text-xs py-1 w-full"
            onClick={() => {
              setSelectedName(null);
              setCreatingNew(true);
            }}
          >
            + New tool
          </button>
        </div>

        <div>
          {selected ? (
            <ToolForm key={selected.name} tool={selected} />
          ) : creatingNew ? (
            <ToolForm
              key="new"
              onSaved={(saved) => {
                setCreatingNew(false);
                setSelectedName(saved.name);
              }}
            />
          ) : (
            <p className="text-sm text-slate-500 py-8 text-center">
              Pick a tool to edit, or create a new one.
            </p>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete tool"
        message={`Delete "${pendingDelete}"? This can't be undone.`}
        confirmLabel="Delete"
        onConfirm={() => pendingDelete && remove.mutate(pendingDelete)}
        onCancel={() => setPendingDelete(null)}
      />
    </section>
  );
}
