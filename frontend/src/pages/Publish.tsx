import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, thumbUrl } from "../api";
import { CopyButton, Markdown, Spinner } from "../components/ui";
import { PolishDialog } from "../components/PolishDialog";
import { useUi } from "../store";

const CHECKLIST = [
  "Title pasted",
  "Description pasted",
  "Tags added",
  "Category set",
  "Licence set",
  "Attribution credited",
  "Images uploaded in order",
  "Print profile attached",
];

export default function PublishPage() {
  const { id = "" } = useParams();
  const queryClient = useQueryClient();
  const notify = useUi((s) => s.notify);

  const { data: project } = useQuery({
    queryKey: ["project", id],
    queryFn: () => api.project(id),
    enabled: Boolean(id),
  });
  const { data: draft, isLoading } = useQuery({
    queryKey: ["publish", id],
    queryFn: () => api.publishDraft(id),
    enabled: Boolean(id),
  });
  const { data: templates } = useQuery({ queryKey: ["templates"], queryFn: api.templates });
  const { data: snippets } = useQuery({ queryKey: ["snippets"], queryFn: api.snippets });
  const { data: llm } = useQuery({ queryKey: ["llm"], queryFn: api.llmStatus });

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [category, setCategory] = useState("");
  const [license, setLicense] = useState("");
  const [template, setTemplate] = useState("default");
  const [assets, setAssets] = useState<string[]>([]);
  const [checklist, setChecklist] = useState<Record<string, boolean>>({});
  const [polishOpen, setPolishOpen] = useState(false);

  // Seed local editor state once the draft arrives.
  useEffect(() => {
    if (!draft) return;
    setTitle(draft.title);
    setDescription(draft.description);
    setTags(draft.tags.join(", "));
    setCategory(draft.category);
    setLicense(draft.license);
    setTemplate(draft.template || "default");
    setAssets(draft.assets);
    setChecklist(draft.checklist ?? {});
  }, [draft]);

  const body = useMemo(
    () => ({
      title,
      description,
      category,
      license,
      template,
      assets,
      checklist,
      tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
    }),
    [title, description, category, license, template, assets, checklist, tags],
  );

  const save = useMutation({
    mutationFn: () => api.savePublishDraft(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["publish", id] });
      notify("Draft saved to publish/makerworld/", "success");
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  const fillTemplate = useMutation({
    mutationFn: () => api.publishPreview(id, template, []),
    onSuccess: (result) => setDescription(result.markdown),
    onError: (error: Error) => notify(error.message, "error"),
  });

  const exportPackage = useMutation({
    mutationFn: async () => {
      await api.savePublishDraft(id, body);
      return api.publishExport(id);
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["publish", id] });
      notify(`Exported to ${result.path} with ${result.assets.length} assets`, "success");
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  // Reachable is not enough — a server with no model chosen cannot polish.
  const polishReady = Boolean(llm?.available && llm.model);

  if (isLoading || !draft || !project) return <Spinner />;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <div className="mr-auto">
          <Link to={`/projects/${id}`} className="text-xs text-slate-500 hover:text-slate-300">
            ← {project.title}
          </Link>
          <h1 className="text-2xl font-semibold mt-1">Publish</h1>
        </div>
        <button className="btn" onClick={() => save.mutate()} disabled={save.isPending}>
          Save draft
        </button>
        <button
          className="btn btn-primary"
          onClick={() => exportPackage.mutate()}
          disabled={exportPackage.isPending}
        >
          {exportPackage.isPending ? "Exporting…" : "Export package"}
        </button>
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="lg:col-span-2 space-y-5">
          <section className="card p-4 space-y-3">
            <div className="flex items-end gap-2">
              <div className="flex-1">
                <label className="label">Title</label>
                <input
                  className="input"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </div>
              <CopyButton value={title} />
            </div>
          </section>

          <section className="card p-4 space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-medium mr-auto">Description</h2>
              <select
                className="input w-auto text-xs py-1"
                value={template}
                onChange={(e) => setTemplate(e.target.value)}
              >
                {templates?.map((t) => (
                  <option key={t.name} value={t.name}>
                    {t.name}
                  </option>
                ))}
              </select>
              <button
                className="btn text-xs py-1"
                onClick={() => fillTemplate.mutate()}
                title="Fill the template from the ingested print data"
              >
                Fill from template
              </button>
              <button
                className="btn text-xs py-1"
                disabled={!polishReady || polishOpen || !description.trim()}
                title={
                  polishReady
                    ? `Tighten with ${llm?.model} on ${llm?.base_url}`
                    : (llm?.reason ?? "Set up a local LLM in Settings")
                }
                onClick={() => setPolishOpen(true)}
              >
                {polishOpen ? "Polishing…" : "Polish with LLM"}
              </button>
              {!polishReady && (
                <Link to="/settings" className="text-xs text-slate-500 hover:text-accent">
                  set up
                </Link>
              )}
              <CopyButton value={description} label="Copy Markdown" />
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <textarea
                className="input font-mono text-sm min-h-[28rem]"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Pick a template above and press Fill."
              />
              <div className="bg-ink-900 border border-ink-600 rounded-lg p-3 overflow-auto max-h-[28rem]">
                <Markdown source={description} />
              </div>
            </div>

            {snippets && snippets.length > 0 && (
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <span className="text-xs text-slate-500">Insert snippet:</span>
                {snippets.map((snippet) => (
                  <button
                    key={snippet.name}
                    className="chip hover:bg-ink-600"
                    onClick={() =>
                      setDescription(
                        (current) => `${current.trimEnd()}\n\n${snippet.body.trim()}\n`,
                      )
                    }
                  >
                    {snippet.name}
                  </button>
                ))}
              </div>
            )}
          </section>

          <section className="card p-4">
            <h2 className="font-medium mb-3">Print profiles</h2>
            {draft.profiles.length === 0 ? (
              <p className="text-sm text-slate-500">
                No prints ingested yet — the settings table fills itself once you drop in a
                sliced 3MF.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs uppercase tracking-wide text-slate-500 text-left">
                      <th className="pb-2 pr-4 font-medium">File</th>
                      <th className="pb-2 pr-4 font-medium">Layer</th>
                      <th className="pb-2 pr-4 font-medium">Walls</th>
                      <th className="pb-2 pr-4 font-medium">Infill</th>
                      <th className="pb-2 pr-4 font-medium">Filament</th>
                      <th className="pb-2 font-medium">Time</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-ink-600">
                    {draft.profiles.map((profile) => (
                      <tr key={profile.name}>
                        <td className="py-2 pr-4 truncate max-w-[12rem]">{profile.name}</td>
                        <td className="py-2 pr-4 font-mono text-xs">
                          {profile.layer_height ?? "—"}
                        </td>
                        <td className="py-2 pr-4 font-mono text-xs">{profile.walls ?? "—"}</td>
                        <td className="py-2 pr-4 font-mono text-xs">
                          {profile.infill ?? "—"} {profile.infill_pattern ?? ""}
                        </td>
                        <td className="py-2 pr-4">{profile.filament || "—"}</td>
                        <td className="py-2">{profile.time}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="card p-4">
            <h2 className="font-medium mb-1">Assets</h2>
            <p className="text-xs text-slate-500 mb-3">
              Click to add, in the order you want to upload them. Exported byte-for-byte into{" "}
              <code className="font-mono">assets/web/</code> and{" "}
              <code className="font-mono">assets/mobile/</code> by their tag — your crops are
              never resampled.
            </p>
            <div className="grid gap-2 grid-cols-4 sm:grid-cols-6">
              {project.images.map((image) => {
                const index = assets.indexOf(image.rel_path);
                return (
                  <button
                    key={image.rel_path}
                    className={`relative rounded-lg overflow-hidden border-2 transition-colors ${
                      index >= 0 ? "border-accent" : "border-ink-600 hover:border-ink-500"
                    }`}
                    onClick={() =>
                      setAssets((current) =>
                        current.includes(image.rel_path)
                          ? current.filter((a) => a !== image.rel_path)
                          : [...current, image.rel_path],
                      )
                    }
                  >
                    <img
                      src={thumbUrl(id, image.rel_path)}
                      alt=""
                      loading="lazy"
                      className="w-full aspect-square object-cover bg-ink-900"
                    />
                    {index >= 0 && (
                      <span className="absolute top-1 left-1 w-5 h-5 rounded-full bg-accent text-ink-900 text-xs font-bold grid place-items-center">
                        {index + 1}
                      </span>
                    )}
                    {image.variant && (
                      <span className="absolute bottom-1 right-1 px-1 rounded text-[10px] font-medium bg-ink-900/85 border border-ink-500">
                        {image.variant}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </section>
        </div>

        <div className="space-y-5">
          <section className="card p-4 space-y-3">
            <h2 className="font-medium">Fields</h2>

            <div>
              <div className="flex items-center justify-between">
                <label className="label mb-0">Tags</label>
                <CopyButton value={tags} />
              </div>
              <input
                className="input mt-1"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
              />
            </div>

            <div>
              <div className="flex items-center justify-between">
                <label className="label mb-0">Category</label>
                <CopyButton value={category} />
              </div>
              <input
                className="input mt-1"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                placeholder="Household"
              />
            </div>

            <div>
              <div className="flex items-center justify-between">
                <label className="label mb-0">Licence</label>
                <CopyButton value={license} />
              </div>
              <input
                className="input mt-1"
                value={license}
                onChange={(e) => setLicense(e.target.value)}
                placeholder="CC-BY-4.0"
              />
            </div>
          </section>

          {project.remix_of.length > 0 && (
            <section className="card p-4">
              <h2 className="font-medium mb-2">Attribution</h2>
              {project.remix_of.map((remix) => (
                <div key={remix.url} className="text-sm mb-2">
                  <p>{remix.title || remix.url}</p>
                  <p className="text-xs text-slate-500">
                    {remix.author && `by ${remix.author}`} {remix.license}
                  </p>
                  <CopyButton value={remix.url} label="Copy URL" />
                </div>
              ))}
            </section>
          )}

          <section className="card p-4">
            <h2 className="font-medium mb-1">Checklist</h2>
            <p className="text-xs text-slate-500 mb-3">Tick these off as you paste.</p>
            <ul className="space-y-1.5">
              {CHECKLIST.map((item) => (
                <li key={item}>
                  <label className="flex items-center gap-2 text-sm cursor-pointer">
                    <input
                      type="checkbox"
                      className="accent-amber-500"
                      checked={checklist[item] ?? false}
                      onChange={(e) =>
                        setChecklist((current) => ({ ...current, [item]: e.target.checked }))
                      }
                    />
                    <span className={checklist[item] ? "line-through text-slate-500" : ""}>
                      {item}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </section>

          {draft.exported_at && (
            <p className="text-xs text-slate-500">
              Last exported {new Date(draft.exported_at).toLocaleString()}
            </p>
          )}
        </div>
      </div>

      <PolishDialog
        open={polishOpen}
        text={description}
        modelLabel={llm?.model}
        onAccept={(polished) => setDescription(polished)}
        onClose={() => setPolishOpen(false)}
      />
    </div>
  );
}
