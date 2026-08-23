import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type MarkdownDoc } from "../api";
import { LlmSettings } from "../components/LlmSettings";
import { Spinner } from "../components/ui";
import { useUi } from "../store";

export default function SettingsPage() {
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: api.health });
  const { data: llm } = useQuery({ queryKey: ["llm"], queryFn: api.llmStatus });
  const { data: jobs } = useQuery({ queryKey: ["jobs"], queryFn: api.jobs });

  if (!health) return <Spinner />;

  return (
    <div className="space-y-5 max-w-4xl">
      <h1 className="text-2xl font-semibold">Settings</h1>

      <section className="card p-4">
        <h2 className="font-medium mb-3">System</h2>
        <dl className="grid grid-cols-[10rem_1fr] gap-y-2 text-sm">
          <Row label="Library" value={health.library_path} mono />
          <Row label="Data" value={health.data_path} mono />
          <Row
            label="Last full scan"
            value={
              health.last_full_scan
                ? new Date(health.last_full_scan).toLocaleString()
                : "not yet"
            }
          />
          <Row label="Watcher" value={health.watcher ? "running" : "disabled"} />
          <Row
            label="Snapshots"
            value={
              health.reflink_available
                ? "copy-on-write clones (cheap)"
                : "full copies — this filesystem has no reflink support"
            }
          />
          <Row
            label="Turntables"
            value={health.render_available ? "available" : "render extras not installed"}
          />
          <Row
            label="LLM"
            value={
              llm?.available
                ? `${llm.provider} · ${llm.model || "no model chosen"} · ${llm.base_url}`
                : (llm?.reason ?? "not configured")
            }
          />
        </dl>
      </section>

      <LlmSettings />

      <MarkdownLibrary
        title="Templates"
        hint="Skeletons filled from ingested print data. Placeholders look like {{settings.layer_height}}."
        queryKey="templates"
        list={api.templates}
        save={api.saveTemplate}
      />

      <MarkdownLibrary
        title="Snippets"
        hint="Reusable blocks — support advice, licensing, troubleshooting, tip jar."
        queryKey="snippets"
        list={api.snippets}
        save={api.saveSnippet}
      />

      {/* Tool management (the SCAD editor's reusable snippets) now lives in
          the editor itself — see EXTRACTION-PROGRESS/04-host-ui.md. Nothing
          to show here when the editor is unavailable; `_shared/tools/` is
          still plain files on disk either way (R13). */}
      {health.editor.available && (
        <section className="card p-4">
          <h2 className="font-medium mb-1">Editor tools</h2>
          <p className="text-sm text-slate-500">
            Reusable OpenSCAD snippets are managed in the editor now.{" "}
            <a
              className="text-accent hover:underline"
              href={`${health.editor.path}settings`}
            >
              Open editor settings →
            </a>
          </p>
        </section>
      )}

      <section className="card p-4">
        <h2 className="font-medium mb-3">Recent jobs</h2>
        {!jobs || jobs.length === 0 ? (
          <p className="text-sm text-slate-500">Nothing has run yet.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {jobs.map((job) => (
              <li key={job.id} className="flex items-center gap-3">
                <span className="font-mono text-xs text-slate-500 w-8">#{job.id}</span>
                <span className="font-mono text-xs">{job.kind}</span>
                <span
                  className={`chip ${
                    job.status === "failed" ? "text-rose-300 border-rose-500/30" : ""
                  }`}
                >
                  {job.status}
                </span>
                <span className="text-xs text-slate-500 truncate">
                  {job.error ?? job.message}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <>
      <dt className="text-slate-500">{label}</dt>
      <dd className={mono ? "font-mono text-xs break-all" : ""}>{value}</dd>
    </>
  );
}

function MarkdownLibrary({
  title,
  hint,
  queryKey,
  list,
  save,
}: {
  title: string;
  hint: string;
  queryKey: string;
  list: () => Promise<MarkdownDoc[]>;
  save: (doc: MarkdownDoc) => Promise<MarkdownDoc>;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [body, setBody] = useState("");
  const [newName, setNewName] = useState("");
  const queryClient = useQueryClient();
  const notify = useUi((s) => s.notify);

  const { data: docs } = useQuery({ queryKey: [queryKey], queryFn: list });

  const persist = useMutation({
    mutationFn: (doc: MarkdownDoc) => save(doc),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [queryKey] });
      notify("Saved", "success");
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  const open = (doc: MarkdownDoc) => {
    setSelected(doc.name);
    setBody(doc.body);
  };

  return (
    <section className="card p-4">
      <h2 className="font-medium">{title}</h2>
      <p className="text-xs text-slate-500 mt-1 mb-3">{hint}</p>

      <div className="grid gap-4 md:grid-cols-[12rem_1fr]">
        <div className="space-y-1">
          {docs?.map((doc) => (
            <button
              key={doc.name}
              onClick={() => open(doc)}
              className={`block w-full text-left px-2 py-1 rounded text-sm ${
                selected === doc.name ? "bg-ink-600 text-white" : "hover:bg-ink-700"
              }`}
            >
              {doc.name}
            </button>
          ))}
          <form
            className="flex gap-1 pt-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (!newName.trim()) return;
              persist.mutate({ name: newName, body: `# ${newName}\n\n` });
              setSelected(newName);
              setBody(`# ${newName}\n\n`);
              setNewName("");
            }}
          >
            <input
              className="input text-xs py-1"
              placeholder="new…"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
            <button className="btn text-xs py-1">+</button>
          </form>
        </div>

        <div>
          {selected ? (
            <>
              <textarea
                className="input font-mono text-sm min-h-[16rem]"
                value={body}
                onChange={(e) => setBody(e.target.value)}
              />
              <div className="flex justify-end mt-2">
                <button
                  className="btn btn-primary"
                  onClick={() => persist.mutate({ name: selected, body })}
                >
                  Save {selected}
                </button>
              </div>
            </>
          ) : (
            <p className="text-sm text-slate-500 py-8 text-center">
              Pick one to edit, or create a new one.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
