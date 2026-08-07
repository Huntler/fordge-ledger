import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type PrintJob, type PrintStatus } from "../api";
import { EmptyState, Modal, Spinner, formatDuration } from "../components/ui";
import { useUi } from "../store";

const COLUMNS: { status: PrintStatus; label: string; tone: string }[] = [
  { status: "queued", label: "Queued", tone: "border-slate-500/40" },
  { status: "printing", label: "Printing", tone: "border-sky-500/40" },
  { status: "done", label: "Done", tone: "border-emerald-500/40" },
  { status: "failed", label: "Failed", tone: "border-rose-500/40" },
];

export default function PrintBoard() {
  const [failing, setFailing] = useState<PrintJob | null>(null);
  const queryClient = useQueryClient();
  const notify = useUi((s) => s.notify);

  const { data: prints, isLoading } = useQuery({
    queryKey: ["prints"],
    queryFn: () => api.prints(),
  });
  const { data: stats } = useQuery({ queryKey: ["print-stats"], queryFn: api.printStats });

  const move = useMutation({
    mutationFn: ({ id, status }: { id: string; status: PrintStatus }) =>
      api.updatePrint(id, { status }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["prints"] });
      queryClient.invalidateQueries({ queryKey: ["print-stats"] });
      queryClient.invalidateQueries({ queryKey: ["failures"] });
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  if (isLoading) return <Spinner />;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-4">
        <h1 className="text-2xl font-semibold mr-auto">Prints</h1>
        {stats && (
          <div className="flex gap-5 text-sm">
            <Stat label="Filament" value={`${stats.filament_g.toFixed(0)} g`} />
            <Stat label="Cost" value={`€${stats.filament_cost.toFixed(2)}`} />
            <Stat label="Time" value={formatDuration(stats.print_seconds)} />
            <Stat
              label="Success"
              value={
                stats.success_rate === null ? "—" : `${Math.round(stats.success_rate * 100)}%`
              }
            />
          </div>
        )}
      </div>

      {!prints || prints.length === 0 ? (
        <EmptyState
          title="No print jobs"
          hint="Open a project and drop a sliced 3MF into its Prints tab."
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {COLUMNS.map((column) => {
            const items = prints.filter((p) => p.status === column.status);
            return (
              <div
                key={column.status}
                className={`card border-t-2 ${column.tone} p-3 space-y-3 min-h-[8rem]`}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  const id = e.dataTransfer.getData("text/plain");
                  const job = prints.find((p) => p.id === id);
                  if (!job || job.status === column.status) return;
                  // A failure needs its reason captured, or the log is useless.
                  if (column.status === "failed") setFailing(job);
                  else move.mutate({ id, status: column.status });
                }}
              >
                <div className="flex items-center justify-between">
                  <h2 className="font-medium text-sm">{column.label}</h2>
                  <span className="text-xs text-slate-500">{items.length}</span>
                </div>

                {items.map((print) => (
                  <PrintCard
                    key={print.id}
                    print={print}
                    onMove={(status) =>
                      status === "failed"
                        ? setFailing(print)
                        : move.mutate({ id: print.id, status })
                    }
                  />
                ))}
              </div>
            );
          })}
        </div>
      )}

      <FailureLog />

      <FailureModal
        job={failing}
        onClose={() => setFailing(null)}
        onSaved={() => {
          queryClient.invalidateQueries({ queryKey: ["prints"] });
          queryClient.invalidateQueries({ queryKey: ["print-stats"] });
          setFailing(null);
        }}
      />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-slate-500">{label}</p>
      <p className="font-medium">{value}</p>
    </div>
  );
}

function PrintCard({
  print,
  onMove,
}: {
  print: PrintJob;
  onMove: (status: PrintStatus) => void;
}) {
  return (
    <div
      draggable
      onDragStart={(e) => e.dataTransfer.setData("text/plain", print.id)}
      className="bg-ink-700 border border-ink-600 rounded-lg p-3 cursor-grab active:cursor-grabbing space-y-2 group"
    >
      <p className="text-sm font-medium leading-tight truncate">{print.name}</p>
      <Link
        to={`/projects/${print.project_id}`}
        className="text-xs text-slate-500 hover:text-accent block truncate"
      >
        {print.project_title}
      </Link>

      <div className="flex flex-wrap gap-1.5 text-xs text-slate-400">
        <span className="chip">{formatDuration(print.actual_s ?? print.estimated_s)}</span>
        {print.weight_g && <span className="chip">{print.weight_g} g</span>}
        {print.filaments[0]?.type && <span className="chip">{print.filaments[0].type}</span>}
      </div>

      {print.failure_reason && (
        <p className="text-xs text-rose-300">{print.failure_reason}</p>
      )}
      {print.failure_fix && (
        <p className="text-xs text-emerald-300">→ {print.failure_fix}</p>
      )}

      {/* Drag works too, but buttons keep the board usable on touch and
          wherever HTML5 drag-and-drop misbehaves. */}
      <div className="flex flex-wrap gap-1 pt-1 border-t border-ink-600">
        {COLUMNS.filter((c) => c.status !== print.status).map((column) => (
          <button
            key={column.status}
            className="btn btn-ghost text-xs px-1.5 py-0.5 text-slate-400 hover:text-slate-100"
            onClick={() => onMove(column.status)}
            title={`Move to ${column.label}`}
          >
            → {column.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function FailureModal({
  job,
  onClose,
  onSaved,
}: {
  job: PrintJob | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [reason, setReason] = useState("");
  const [fix, setFix] = useState("");
  const notify = useUi((s) => s.notify);

  const save = useMutation({
    mutationFn: () =>
      api.updatePrint(job!.id, {
        status: "failed",
        failure_reason: reason,
        failure_fix: fix,
      }),
    onSuccess: () => {
      setReason("");
      setFix("");
      onSaved();
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  return (
    <Modal open={Boolean(job)} title="What went wrong?" onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <p className="text-sm text-slate-400">
          This is the part that pays off later — it turns “why did this warp last time” into a
          query.
        </p>
        <div>
          <label className="label">What happened</label>
          <input
            className="input"
            autoFocus
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Corner lifted off the plate at ~15mm"
          />
        </div>
        <div>
          <label className="label">What you changed</label>
          <input
            className="input"
            value={fix}
            onChange={(e) => setFix(e.target.value)}
            placeholder="5mm brim, bed to 60C"
          />
        </div>
        <div className="flex justify-end gap-2">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={save.isPending}>
            Mark failed
          </button>
        </div>
      </form>
    </Modal>
  );
}

function FailureLog() {
  const { data: failures } = useQuery({ queryKey: ["failures"], queryFn: api.failures });
  if (!failures || failures.length === 0) return null;

  return (
    <section className="card p-4">
      <h2 className="font-medium mb-3">Failure log</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs uppercase tracking-wide text-slate-500 text-left">
              <th className="pb-2 pr-4 font-medium">Print</th>
              <th className="pb-2 pr-4 font-medium">Settings</th>
              <th className="pb-2 pr-4 font-medium">What happened</th>
              <th className="pb-2 font-medium">What you changed</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-600">
            {failures.map((failure) => (
              <tr key={failure.id} className="align-top">
                <td className="py-2 pr-4">
                  <p className="truncate max-w-[14rem]">{failure.name}</p>
                  <Link
                    to={`/projects/${failure.project_id}`}
                    className="text-xs text-slate-500 hover:text-accent"
                  >
                    {failure.project_title}
                  </Link>
                </td>
                <td className="py-2 pr-4 text-xs text-slate-400 font-mono whitespace-nowrap">
                  {failure.settings.layer_height ?? "—"} ·{" "}
                  {failure.settings.infill_density ?? "—"}
                </td>
                <td className="py-2 pr-4 text-slate-300">{failure.failure_reason ?? "—"}</td>
                <td className="py-2 text-emerald-300">{failure.failure_fix ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
