import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { ToolFormDialog } from "./ToolForm";

/**
 * Row of reusable OpenSCAD snippets ("tools") below the editor+preview on
 * the Editor page — click one to toggle it: copies the tool's .scad into
 * this project's models/sources/tools/ and adds a `use <tools/slug.scad>;`
 * line at the top of the buffer (see ScadWorkspace's toggleTool), making
 * its modules/functions callable by name rather than inlining its body.
 * Click again to remove both. Tools currently added to the open file are
 * highlighted. Tools are library-wide, not per-project; create new ones
 * here via "+" or manage the full set from Settings.
 */
export function ScadToolbar({
  addedTools,
  onToggle,
  className = "",
}: {
  addedTools: Set<string>;
  onToggle: (toolName: string) => void;
  className?: string;
}) {
  const [creating, setCreating] = useState(false);
  const { data: tools } = useQuery({ queryKey: ["tools"], queryFn: api.tools });

  return (
    <div className={`card p-2 flex items-center gap-2 overflow-x-auto ${className}`}>
      <span className="px-1 text-xs font-medium uppercase tracking-wide text-slate-500 shrink-0">
        Tools
      </span>
      {(tools ?? []).map((tool) => {
        const added = addedTools.has(tool.name);
        return (
          <button
            key={tool.name}
            type="button"
            className={`btn shrink-0 px-2 py-1.5 ${
              added ? "border-accent bg-accent/15 text-accent-soft" : "btn-ghost"
            }`}
            title={added ? `${tool.name} (added — click to remove)` : tool.name}
            onClick={() => onToggle(tool.name)}
          >
            {tool.has_icon ? (
              <img
                src={api.toolIconUrl(tool.name)}
                alt=""
                className="w-5 h-5 rounded object-cover"
              />
            ) : (
              <span className="w-5 h-5 rounded bg-ink-600 flex items-center justify-center text-[10px] font-medium">
                {tool.name.slice(0, 2).toUpperCase()}
              </span>
            )}
          </button>
        );
      })}
      <button
        type="button"
        className="btn btn-ghost shrink-0 px-2 py-1.5"
        title="New tool"
        onClick={() => setCreating(true)}
      >
        +
      </button>
      <ToolFormDialog open={creating} onClose={() => setCreating(false)} />
    </div>
  );
}
