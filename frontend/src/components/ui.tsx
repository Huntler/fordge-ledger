import { useEffect, useRef, useState, type ReactNode } from "react";
import { useUi } from "../store";
import type { PrintStatus, ProjectStatus } from "../api";

export function formatDuration(seconds: number | null | undefined): string {
  if (!seconds) return "—";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours && minutes) return `${hours}h ${minutes}m`;
  if (hours) return `${hours}h`;
  return `${minutes}m`;
}

export function formatSize(bytes: number): string {
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit++;
  }
  return `${unit === 0 ? value : value.toFixed(1)} ${units[unit]}`;
}

const PROJECT_TONES: Record<ProjectStatus, string> = {
  idea: "bg-slate-500/15 text-slate-300 border-slate-500/30",
  designing: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  testing: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  ready: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  published: "bg-violet-500/15 text-violet-300 border-violet-500/30",
  shelved: "bg-ink-600 text-slate-400 border-ink-500",
};

const PRINT_TONES: Record<PrintStatus, string> = {
  queued: "bg-slate-500/15 text-slate-300 border-slate-500/30",
  printing: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  done: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  failed: "bg-rose-500/15 text-rose-300 border-rose-500/30",
};

export function StatusBadge({ status }: { status: ProjectStatus | PrintStatus }) {
  const tone =
    PROJECT_TONES[status as ProjectStatus] ?? PRINT_TONES[status as PrintStatus] ?? "";
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium border ${tone}`}>
      {status}
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-slate-400 py-8 justify-center">
      <span className="w-4 h-4 border-2 border-ink-500 border-t-accent rounded-full animate-spin" />
      {label ?? "Loading…"}
    </div>
  );
}

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="text-center py-12 px-4">
      <p className="text-slate-300 font-medium">{title}</p>
      {hint && <p className="text-sm text-slate-500 mt-1 max-w-md mx-auto">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Modal({
  open,
  title,
  onClose,
  children,
  wide,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className={`card w-full ${wide ? "max-w-4xl" : "max-w-lg"} max-h-[85vh] overflow-auto`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-ink-600 sticky top-0 bg-ink-800">
          <h2 className="font-semibold">{title}</h2>
          <button className="btn btn-ghost px-2 py-1" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

/** Proceed-warning for a destructive action that has no undo — a hover ✕ alone isn't enough. */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Delete",
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Modal open={open} title={title} onClose={onCancel}>
      <p className="text-sm text-slate-300">{message}</p>
      <div className="flex justify-end gap-2 pt-4">
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className="btn border-rose-500/40 text-rose-300 hover:bg-rose-500/10"
          onClick={onConfirm}
        >
          {confirmLabel}
        </button>
      </div>
    </Modal>
  );
}

/** Copy button for the publish workspace — the whole point is pasting into MakerWorld. */
export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number>();

  useEffect(() => () => window.clearTimeout(timer.current), []);

  return (
    <button
      className="btn text-xs py-1"
      disabled={!value}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
        } catch {
          // Clipboard needs a secure context; over plain HTTP on the LAN it is
          // unavailable, so fall back to a selection the user can copy.
          const area = document.createElement("textarea");
          area.value = value;
          document.body.appendChild(area);
          area.select();
          document.execCommand("copy");
          area.remove();
        }
        setCopied(true);
        timer.current = window.setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? "Copied ✓" : label}
    </button>
  );
}

export function Toasts() {
  const toasts = useUi((s) => s.toasts);
  const dismiss = useUi((s) => s.dismiss);

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 w-80">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          onClick={() => dismiss(toast.id)}
          className={`card px-4 py-3 text-sm cursor-pointer shadow-lg ${
            toast.tone === "error"
              ? "border-rose-500/40 text-rose-200"
              : toast.tone === "success"
                ? "border-emerald-500/40 text-emerald-200"
                : ""
          }`}
        >
          {toast.message}
        </div>
      ))}
    </div>
  );
}

export function JobProgress() {
  const jobs = useUi((s) => s.jobs);
  const entries = Object.entries(jobs);
  if (entries.length === 0) return null;

  return (
    <div className="fixed bottom-4 left-4 z-40 flex flex-col gap-2 w-72">
      {entries.map(([id, job]) => (
        <div key={id} className="card px-4 py-3 shadow-lg">
          <div className="flex justify-between text-xs text-slate-400 mb-1.5">
            <span className="font-mono">{job.kind}</span>
            <span>{Math.round(job.progress * 100)}%</span>
          </div>
          <div className="h-1.5 bg-ink-900 rounded-full overflow-hidden">
            <div
              className="h-full bg-accent transition-all duration-300"
              style={{ width: `${job.progress * 100}%` }}
            />
          </div>
          {job.message && (
            <p className="text-xs text-slate-500 mt-1.5 truncate">{job.message}</p>
          )}
        </div>
      ))}
    </div>
  );
}

/**
 * Instant, app-styled hover tooltip — a from-scratch stand-in for the
 * native `title` attribute, which imposes its own show delay and can't
 * hold anything richer than plain text. `position: fixed`, recomputed from
 * the anchor's own rect on each show, so — like Modal's backdrop above —
 * it escapes clipping from an `overflow-auto` ancestor (e.g. ScadToolbar's
 * horizontally-scrolling tool strip) without needing a portal.
 */
export function Tooltip({ content, children }: { content: ReactNode; children: ReactNode }) {
  const anchorRef = useRef<HTMLSpanElement>(null);
  const [rect, setRect] = useState<DOMRect | null>(null);

  const show = () => setRect(anchorRef.current?.getBoundingClientRect() ?? null);
  const hide = () => setRect(null);

  // Keeps the tooltip glued to its anchor if the strip scrolls underneath
  // it without the mouse leaving — e.g. a trackpad swipe mid-hover.
  useEffect(() => {
    if (!rect) return;
    window.addEventListener("scroll", show, true);
    return () => window.removeEventListener("scroll", show, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-subscribe on visibility, not on every rect update
  }, [rect !== null]);

  return (
    <span
      ref={anchorRef}
      className="inline-flex shrink-0"
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      {children}
      {rect && (
        <div
          className="fixed z-50 max-w-md rounded-lg border border-accent bg-ink-800 px-3 py-2
                     text-xs shadow-lg pointer-events-none"
          style={{ left: rect.left + rect.width / 2, top: rect.top - 8, transform: "translate(-50%, -100%)" }}
        >
          {content}
        </div>
      )}
    </span>
  );
}

/** Minimal Markdown preview — enough for headings, lists, bold and links. */
export function Markdown({ source }: { source: string }) {
  const html = source
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/^### (.*)$/gm, '<h3 class="font-semibold mt-4 mb-1">$1</h3>')
    .replace(/^## (.*)$/gm, '<h2 class="font-semibold text-lg mt-5 mb-2">$1</h2>')
    .replace(/^# (.*)$/gm, '<h1 class="font-bold text-xl mt-2 mb-3">$1</h1>')
    .replace(/^---$/gm, '<hr class="my-4 border-ink-600" />')
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\[(.+?)\]\((.+?)\)/g, '<a class="text-accent underline" href="$2">$1</a>')
    .replace(/^- (.*)$/gm, '<li class="ml-5 list-disc">$1</li>')
    .replace(/\n{2,}/g, '</p><p class="my-2">');

  return (
    <div
      className="text-sm leading-relaxed text-slate-300"
      dangerouslySetInnerHTML={{ __html: `<p class="my-2">${html}</p>` }}
    />
  );
}
