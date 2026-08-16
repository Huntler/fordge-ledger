import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type Tool } from "../api";
import { useUi } from "../store";
import { DropZone } from "./DropZone";
import { Modal } from "./ui";

const MAX_ICON_SIZE = 512;

function readImageDimensions(file: File): Promise<{ width: number; height: number }> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve({ width: img.naturalWidth, height: img.naturalHeight });
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("not a readable image"));
    };
    img.src = url;
  });
}

/**
 * Shared name/snippet/icon form for a tool — reused both inside the
 * toolbar's "+" popup (see ToolFormDialog below) and inline in Settings for
 * editing an existing one. The icon is checked client-side (square, up to
 * 512x512) before upload so a bad image is rejected immediately rather than
 * after a round-trip; the backend (services/tools.py) checks again, since
 * this is the only thing standing between it and a corrupt file otherwise.
 */
export function ToolForm({
  tool,
  onSaved,
}: {
  /** Omit to create a new tool; pass an existing one to edit it in place. */
  tool?: Tool;
  onSaved?: (tool: Tool) => void;
}) {
  const notify = useUi((s) => s.notify);
  const queryClient = useQueryClient();

  const [name, setName] = useState(tool?.name ?? "");
  const [body, setBody] = useState(tool?.body ?? "");
  const [icon, setIcon] = useState<File | null>(null);
  const [iconPreview, setIconPreview] = useState<string | null>(
    tool?.has_icon ? api.toolIconUrl(tool.name) : null,
  );

  const save = useMutation({
    mutationFn: () => {
      if (!name.trim()) throw new Error("Name is required");
      return api.saveTool(name.trim(), body, icon);
    },
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ["tools"] });
      notify(`Saved ${saved.name}`, "success");
      onSaved?.(saved);
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  const pickIcon = async (files: File[]) => {
    const file = files[0];
    if (!file) return;
    try {
      const { width, height } = await readImageDimensions(file);
      if (width !== height) {
        notify(`Icon must be square (got ${width}x${height})`, "error");
        return;
      }
      if (width > MAX_ICON_SIZE) {
        notify(`Icon must be ${MAX_ICON_SIZE}x${MAX_ICON_SIZE} or smaller`, "error");
        return;
      }
      setIcon(file);
      setIconPreview(URL.createObjectURL(file));
    } catch {
      notify("Not a readable image", "error");
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <label className="label">Name</label>
        <input
          className="input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Screw"
        />
      </div>

      <div>
        <label className="label">Icon</label>
        <div className="flex items-center gap-3">
          {iconPreview ? (
            <img
              src={iconPreview}
              alt=""
              className="w-12 h-12 rounded-lg border border-ink-600 object-cover shrink-0"
            />
          ) : (
            <div className="w-12 h-12 rounded-lg border border-ink-600 bg-ink-900 flex items-center justify-center text-sm text-slate-500 shrink-0">
              {(name || "?").slice(0, 2).toUpperCase()}
            </div>
          )}
          <div className="flex-1">
            <DropZone
              compact
              accept="image/*"
              title="Drop or click to choose an icon"
              hint="Square image, up to 512x512"
              onFiles={pickIcon}
            />
          </div>
        </div>
      </div>

      <div>
        <label className="label">Snippet</label>
        <textarea
          className="input font-mono text-sm min-h-[10rem]"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder='module screw(length, diameter, head="flat", head_diameter, head_height=0, chamfer=0) { ... }'
        />
      </div>

      <div className="flex justify-end">
        <button
          type="button"
          className="btn btn-primary"
          disabled={save.isPending}
          onClick={() => save.mutate()}
        >
          {save.isPending ? "Saving…" : "Save tool"}
        </button>
      </div>
    </div>
  );
}

/** The "+" popup on the Editor page's tools toolbar — create a tool without leaving the page. */
export function ToolFormDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} title="New tool" onClose={onClose}>
      <ToolForm onSaved={onClose} />
    </Modal>
  );
}
