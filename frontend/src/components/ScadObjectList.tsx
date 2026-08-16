import type { ScadObject } from "../lib/scadObjects";

/**
 * Vertical stack of the SCAD buffer's top-level objects, to the left of the
 * STL preview (see ScadWorkspace) — one row per geometry-producing
 * statement (computeScadObjects), in source order. Clicking a row comments
 * its statement out of the buffer, or restores it if it's already commented
 * out — the dimmed, struck-through styling on an inactive row is that same
 * click's visual echo, not a separate state to track.
 *
 * Each row also carries a small thumbnail — that object rendered alone, at
 * low quality (see ScadWorkspace's generateThumbnails) — so deciding what
 * to hide doesn't mean cross-referencing the label against the 3D view.
 * `thumbnails` is keyed by an object's own statement text (ScadObject.text,
 * trimmed); a row whose thumbnail hasn't landed yet (or never will — a
 * blank object, or one whose isolated render failed) just shows a
 * placeholder square instead of blocking on it.
 */
export function ScadObjectList({
  objects,
  thumbnails,
  onToggle,
  className = "",
}: {
  objects: ScadObject[];
  thumbnails: Record<string, string>;
  onToggle: (object: ScadObject) => void;
  className?: string;
}) {
  return (
    <div className={`min-h-0 overflow-auto border-r border-ink-600 bg-ink-900/40 ${className}`}>
      <div className="px-3 py-2 text-xs font-medium uppercase tracking-wide text-slate-500 sticky top-0 bg-ink-900/80 backdrop-blur border-b border-ink-600">
        Objects
      </div>
      {objects.length === 0 ? (
        <p className="px-3 py-3 text-xs text-slate-500">Nothing rendered yet.</p>
      ) : (
        <ul className="p-1.5 space-y-1">
          {objects.map((object) => {
            const thumbnail = thumbnails[object.text.trim()];
            return (
              <li key={`${object.from}-${object.to}`}>
                <button
                  type="button"
                  title={object.active ? "Click to comment out" : "Click to restore"}
                  onClick={() => onToggle(object)}
                  className={`w-full flex items-center gap-2 px-1.5 py-1.5 rounded-md text-xs font-mono border transition-opacity ${
                    object.active
                      ? "border-transparent hover:border-ink-600 hover:bg-ink-700/50 text-slate-200"
                      : "border-dashed border-ink-600 text-slate-500 opacity-50 line-through"
                  }`}
                >
                  {thumbnail ? (
                    <img
                      src={thumbnail}
                      alt=""
                      className="w-8 h-8 shrink-0 rounded bg-ink-900 object-contain"
                    />
                  ) : (
                    <span className="w-8 h-8 shrink-0 rounded bg-ink-800" aria-hidden="true" />
                  )}
                  <span className="truncate">{object.label || "(object)"}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
