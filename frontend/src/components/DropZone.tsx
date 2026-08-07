import { useCallback, useRef, useState, type ReactNode } from "react";

/**
 * Drag-and-drop target that also works as a click-to-browse button.
 *
 * Counts enter/leave events rather than toggling a boolean: dragging over a
 * child element fires dragleave on the parent, which would otherwise make the
 * highlight flicker the whole time the cursor is inside.
 */
export function DropZone({
  onFiles,
  accept,
  busy,
  title,
  hint,
  compact,
  children,
}: {
  onFiles: (files: File[]) => void;
  accept?: string;
  busy?: boolean;
  title: string;
  hint?: string;
  compact?: boolean;
  children?: ReactNode;
}) {
  const [over, setOver] = useState(false);
  const depth = useRef(0);
  const input = useRef<HTMLInputElement>(null);

  const reset = useCallback(() => {
    depth.current = 0;
    setOver(false);
  }, []);

  const take = useCallback(
    (list: FileList | null) => {
      const files = Array.from(list ?? []);
      if (files.length) onFiles(files);
    },
    [onFiles],
  );

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => input.current?.click()}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          input.current?.click();
        }
      }}
      onDragEnter={(e) => {
        e.preventDefault();
        depth.current += 1;
        setOver(true);
      }}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={(e) => {
        e.preventDefault();
        depth.current -= 1;
        if (depth.current <= 0) reset();
      }}
      onDrop={(e) => {
        e.preventDefault();
        reset();
        take(e.dataTransfer.files);
      }}
      className={`card border-dashed cursor-pointer transition-colors text-center ${
        compact ? "p-4" : "p-8"
      } ${over ? "border-accent bg-accent/5" : "hover:border-ink-500"} ${
        busy ? "opacity-60 pointer-events-none" : ""
      }`}
    >
      <p className="text-slate-300 text-sm font-medium">{busy ? "Uploading…" : title}</p>
      {hint && <p className="text-xs text-slate-500 mt-1">{hint}</p>}
      {children}
      <input
        ref={input}
        type="file"
        multiple
        accept={accept}
        className="hidden"
        onChange={(e) => {
          take(e.target.files);
          e.target.value = "";
        }}
      />
    </div>
  );
}
