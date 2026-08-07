import { useCallback, useEffect, useRef, useState } from "react";
import { api, type PolishRun } from "../api";
import { Modal } from "./ui";

const POLL_MS = 400;

/**
 * Runs a polish and shows what the model is doing while it does it.
 *
 * A local model routinely takes 15–60s, so this opens the moment you click and
 * stays up until there is something to accept. Closing it — by button, ✕ or
 * Escape — cancels the run rather than abandoning it, which actually stops the
 * generation on the LLM machine instead of leaving it churning.
 */
export function PolishDialog({
  open,
  text,
  instructions = "",
  modelLabel,
  onAccept,
  onClose,
}: {
  open: boolean;
  text: string;
  instructions?: string;
  modelLabel?: string;
  onAccept: (polished: string) => void;
  onClose: () => void;
}) {
  const [run, setRun] = useState<PolishRun | null>(null);
  const [error, setError] = useState("");
  const runId = useRef<string | null>(null);
  const timer = useRef<number>();

  const stopPolling = useCallback(() => {
    window.clearInterval(timer.current);
    timer.current = undefined;
  }, []);

  // Cancel server-side too, so the model stops generating.
  const cancelAndClose = useCallback(() => {
    stopPolling();
    const id = runId.current;
    runId.current = null;
    if (id) void api.cancelPolish(id).catch(() => undefined);
    onClose();
  }, [onClose, stopPolling]);

  useEffect(() => {
    if (!open) return;

    let disposed = false;
    setRun(null);
    setError("");

    api
      .startPolish(text, instructions)
      .then((started) => {
        if (disposed) {
          // Closed before the run even registered; do not orphan it.
          void api.cancelPolish(started.id).catch(() => undefined);
          return;
        }
        runId.current = started.id;
        setRun(started);

        timer.current = window.setInterval(async () => {
          const id = runId.current;
          if (!id) return;
          try {
            const next = await api.pollPolish(id);
            setRun(next);
            if (next.status !== "running") {
              stopPolling();
              runId.current = null;
            }
          } catch (err) {
            stopPolling();
            runId.current = null;
            setError(err instanceof Error ? err.message : String(err));
          }
        }, POLL_MS);
      })
      .catch((err: Error) => {
        if (!disposed) setError(err.message);
      });

    return () => {
      disposed = true;
      stopPolling();
      const id = runId.current;
      runId.current = null;
      if (id) void api.cancelPolish(id).catch(() => undefined);
    };
  }, [open, text, instructions, stopPolling]);

  const status = error ? "failed" : (run?.status ?? "running");
  const elapsed = run?.elapsed_seconds ?? 0;

  return (
    <Modal
      open={open}
      title={status === "done" ? "LLM polish — accept or reject" : "Polishing with the LLM"}
      onClose={cancelAndClose}
      wide={status === "done"}
    >
      {status === "running" && (
        <div className="space-y-5 py-2">
          <div className="flex items-center gap-3">
            <span className="w-5 h-5 border-2 border-ink-500 border-t-accent rounded-full animate-spin shrink-0" />
            <div className="min-w-0">
              <p className="text-sm text-slate-200">
                Working{modelLabel ? ` — ${modelLabel}` : ""}…
              </p>
              <p className="text-xs text-slate-500 mt-0.5 tabular-nums">
                {elapsed.toFixed(1)}s elapsed. A local model usually takes 15–60 seconds.
              </p>
            </div>
          </div>

          {/* Indeterminate: neither provider reports progress for a non-streamed
              completion, so a percentage here would be a lie. */}
          <div className="h-1 bg-ink-900 rounded-full overflow-hidden">
            <div className="h-full w-1/3 bg-accent rounded-full animate-[polish-sweep_1.4s_ease-in-out_infinite]" />
          </div>

          <div className="flex justify-end">
            <button className="btn" onClick={cancelAndClose}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {status === "failed" && (
        <div className="space-y-4 py-2">
          <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2">
            <p className="text-sm text-rose-200">
              {error || run?.error || "The LLM request failed."}
            </p>
            <p className="text-xs text-rose-300/70 mt-1">
              Your text is untouched. Check the server in Settings and try again.
            </p>
          </div>
          <div className="flex justify-end">
            <button className="btn" onClick={cancelAndClose}>
              Close
            </button>
          </div>
        </div>
      )}

      {status === "cancelled" && (
        <div className="space-y-4 py-2">
          <p className="text-sm text-slate-300">Cancelled. Nothing was changed.</p>
          <div className="flex justify-end">
            <button className="btn" onClick={cancelAndClose}>
              Close
            </button>
          </div>
        </div>
      )}

      {status === "done" && run && (
        <div className="space-y-4">
          <p className="text-xs text-slate-500">
            Took {elapsed.toFixed(1)}s{modelLabel ? ` on ${modelLabel}` : ""}. Nothing is
            applied until you accept.
          </p>
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <p className="label">Yours</p>
              <pre className="bg-ink-900 border border-ink-600 rounded-lg p-3 text-xs whitespace-pre-wrap max-h-96 overflow-auto">
                {run.original}
              </pre>
            </div>
            <div>
              <p className="label">Polished</p>
              <pre className="bg-ink-900 border border-emerald-500/30 rounded-lg p-3 text-xs whitespace-pre-wrap max-h-96 overflow-auto">
                {run.polished}
              </pre>
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <button className="btn" onClick={cancelAndClose}>
              Reject
            </button>
            <button
              className="btn btn-primary"
              onClick={() => {
                onAccept(run.polished ?? "");
                onClose();
              }}
            >
              Accept
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
