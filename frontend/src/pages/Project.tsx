import { useRef, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  fileUrl,
  thumbUrl,
  type ImageVariant,
  type Project,
  type ProjectFile,
  type ProjectStatus,
  type SourceFile,
} from "../api";
import { DropZone } from "../components/DropZone";
import {
  ConfirmDialog,
  EmptyState,
  Modal,
  Spinner,
  StatusBadge,
  formatDuration,
  formatSize,
} from "../components/ui";
import { useUi } from "../store";

const STATUSES: ProjectStatus[] = [
  "idea",
  "designing",
  "testing",
  "ready",
  "published",
  "shelved",
];

const TABS = ["Files", "Sources", "Prints", "Images", "Versions", "Notes"] as const;
type Tab = (typeof TABS)[number];

export default function ProjectPage() {
  const { id = "" } = useParams();
  const [tab, setTab] = useState<Tab>("Files");

  const { data: project, isLoading } = useQuery({
    queryKey: ["project", id],
    queryFn: () => api.project(id),
    enabled: Boolean(id),
  });

  if (isLoading) return <Spinner />;
  if (!project) return <EmptyState title="Project not found" />;

  return (
    <div className="space-y-5">
      <Header project={project} />

      <div className="flex gap-1 border-b border-ink-600">
        {TABS.map((name) => (
          <button
            key={name}
            onClick={() => setTab(name)}
            className={`px-4 py-2 text-sm border-b-2 -mb-px transition-colors ${
              tab === name
                ? "border-accent text-white"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            {name}
            {name === "Files" && project.unfiled.length > 0 && (
              <span className="ml-1.5 text-xs text-amber-400">{project.unfiled.length}</span>
            )}
          </button>
        ))}
      </div>

      {tab === "Files" && <FilesTab project={project} />}
      {tab === "Sources" && <SourcesTab project={project} />}
      {tab === "Prints" && <PrintsTab project={project} />}
      {tab === "Images" && <ImagesTab project={project} />}
      {tab === "Versions" && <VersionsTab project={project} />}
      {tab === "Notes" && <NotesTab project={project} />}
    </div>
  );
}

function Header({ project }: { project: Project }) {
  const [editing, setEditing] = useState(false);
  const queryClient = useQueryClient();
  const notify = useUi((s) => s.notify);

  const update = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.updateProject(project.id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["project", project.id] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  return (
    <div className="flex flex-wrap items-start gap-4">
      <div className="flex-1 min-w-0">
        <Link to="/library" className="text-xs text-slate-500 hover:text-slate-300">
          ← Library
        </Link>
        <h1 className="text-2xl font-semibold mt-1">{project.title}</h1>
        <p className="text-xs text-slate-500 font-mono mt-1">{project.slug}/</p>

        <div className="flex flex-wrap items-center gap-2 mt-3">
          {project.tags.map((tag) => (
            <span key={tag} className="chip">
              {tag}
            </span>
          ))}
          {project.license && <span className="chip">{project.license}</span>}
        </div>
      </div>

      <div className="flex items-center gap-2">
        <select
          className="input w-auto"
          value={project.status}
          onChange={(e) => update.mutate({ status: e.target.value })}
        >
          {STATUSES.map((status) => (
            <option key={status} value={status}>
              {status}
            </option>
          ))}
        </select>
        <button className="btn" onClick={() => setEditing(true)}>
          Edit
        </button>
        <Link className="btn btn-primary" to={`/projects/${project.id}/publish`}>
          Publish
        </Link>
      </div>

      <EditModal
        project={project}
        open={editing}
        onClose={() => setEditing(false)}
        onSave={(body) => update.mutate(body)}
      />
    </div>
  );
}

function EditModal({
  project,
  open,
  onClose,
  onSave,
}: {
  project: Project;
  open: boolean;
  onClose: () => void;
  onSave: (body: Record<string, unknown>) => void;
}) {
  const [title, setTitle] = useState(project.title);
  const [tags, setTags] = useState(project.tags.join(", "));
  const [license, setLicense] = useState(project.license);
  const [remixes, setRemixes] = useState(project.remix_of);
  const [makerworldUrl, setMakerworldUrl] = useState(project.makerworld_url ?? "");

  return (
    <Modal open={open} title="Edit project" onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          onSave({
            title,
            license,
            tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
            remix_of: remixes.filter((r) => r.url.trim()),
            makerworld_url: makerworldUrl.trim() || undefined,
          });
          onClose();
        }}
      >
        <div>
          <label className="label">Title</label>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
          <p className="text-xs text-slate-500 mt-1">Renaming also moves the folder.</p>
        </div>

        <div>
          <label className="label">Tags</label>
          <input className="input" value={tags} onChange={(e) => setTags(e.target.value)} />
        </div>

        <div>
          <label className="label">Licence</label>
          <input
            className="input"
            value={license}
            onChange={(e) => setLicense(e.target.value)}
            placeholder="CC-BY-4.0"
          />
        </div>

        <div>
          <label className="label">Remixed from</label>
          <p className="text-xs text-slate-500 mb-2">
            Recorded now so credits never have to be reconstructed from memory.
          </p>
          <div className="space-y-2">
            {remixes.map((remix, index) => (
              <div key={index} className="grid grid-cols-2 gap-2">
                <input
                  className="input col-span-2"
                  placeholder="https://makerworld.com/en/models/…"
                  value={remix.url}
                  onChange={(e) =>
                    setRemixes(
                      remixes.map((r, i) => (i === index ? { ...r, url: e.target.value } : r)),
                    )
                  }
                />
                <input
                  className="input"
                  placeholder="Original title"
                  value={remix.title ?? ""}
                  onChange={(e) =>
                    setRemixes(
                      remixes.map((r, i) => (i === index ? { ...r, title: e.target.value } : r)),
                    )
                  }
                />
                <input
                  className="input"
                  placeholder="Author"
                  value={remix.author ?? ""}
                  onChange={(e) =>
                    setRemixes(
                      remixes.map((r, i) => (i === index ? { ...r, author: e.target.value } : r)),
                    )
                  }
                />
              </div>
            ))}
          </div>
          <button
            type="button"
            className="btn btn-ghost text-xs mt-2"
            onClick={() => setRemixes([...remixes, { url: "", title: "", author: "" }])}
          >
            + Add source
          </button>
        </div>

        <div>
          <label className="label">Makerworld URL</label>
          <p className="text-xs text-slate-500 mt-1">Published model link on makerworld.com.</p>
          <input
            className="input"
            value={makerworldUrl}
            onChange={(e) => setMakerworldUrl(e.target.value)}
            placeholder="https://makerworld.com/en/models/…"
          />
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary">Save</button>
        </div>
      </form>

      <DeleteProject project={project} onDeleted={onClose} />
    </Modal>
  );
}

const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;

/**
 * Delete, tucked below the edit form behind a deliberate second step.
 *
 * The folder moves to `_trash/` rather than being destroyed, so this is
 * recoverable — but it still takes the whole project out of the library, which
 * is worth typing a name for.
 */
function DeleteProject({
  project,
  onDeleted,
}: {
  project: Project;
  onDeleted: () => void;
}) {
  const [arming, setArming] = useState(false);
  const [typed, setTyped] = useState("");
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const notify = useUi((s) => s.notify);

  const remove = useMutation({
    mutationFn: () => api.deleteProject(project.id),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.removeQueries({ queryKey: ["project", project.id] });
      notify(`Deleted ${result.title}. The folder is in _trash/.`, "success");
      onDeleted();
      navigate("/library");
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  const confirmed = typed.trim() === project.title.trim();

  return (
    <div className="mt-6 pt-4 border-t border-ink-600">
      {!arming ? (
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm text-slate-300">Delete this project</p>
            <p className="text-xs text-slate-500 mt-0.5">
              Moves the whole folder to <code className="font-mono">_trash/</code>.
            </p>
          </div>
          <button
            type="button"
            className="btn border-rose-500/40 text-rose-300 hover:bg-rose-500/10"
            onClick={() => setArming(true)}
          >
            Delete…
          </button>
        </div>
      ) : (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/5 p-3 space-y-3">
          <div>
            <p className="text-sm text-rose-200">
              Delete “{project.title}” and everything in it?
            </p>
            <p className="text-xs text-slate-400 mt-1">
              {count(project.models.length, "model")}, {count(project.prints.length, "print")},{" "}
              {count(project.images.length, "image")} and{" "}
              {count(project.versions.length, "version")}. The folder moves to{" "}
              <code className="font-mono">_trash/</code> — nothing is destroyed, and moving it
              back and rescanning restores the project.
            </p>
          </div>

          <div>
            <label className="label">Type the title to confirm</label>
            {/* Outside the label, which is uppercased — the title has to be
                shown exactly as it must be typed. */}
            <p className="text-sm font-mono text-slate-300 -mt-0.5 mb-1.5">{project.title}</p>
            <input
              className="input"
              autoFocus
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={project.title}
            />
          </div>

          <div className="flex justify-end gap-2">
            <button
              type="button"
              className="btn"
              onClick={() => {
                setArming(false);
                setTyped("");
              }}
            >
              Keep it
            </button>
            <button
              type="button"
              className="btn bg-rose-600 border-rose-600 text-white hover:bg-rose-500 disabled:hover:bg-rose-600"
              disabled={!confirmed || remove.isPending}
              onClick={() => remove.mutate()}
            >
              {remove.isPending ? "Deleting…" : "Move to trash"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

const KIND_ICONS: Record<string, string> = {
  cad: "✎",
  mesh: "△",
  sliced: "⧉",
  image: "▣",
  image_source: "◈",
  doc: "≡",
  misc: "⧉",
  other: "·",
};

function FilesTab({ project }: { project: Project }) {
  const queryClient = useQueryClient();
  const notify = useUi((s) => s.notify);
  const [attaching, setAttaching] = useState<string | null>(null);

  const attach = useMutation({
    mutationFn: ({ model, file }: { model: string; file: string }) =>
      api.attachFiles(project.id, model, [file]),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["project", project.id] });
      notify("Filed", "success");
      setAttaching(null);
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  const grouped = project.files.reduce<Record<string, ProjectFile[]>>((acc, file) => {
    (acc[file.kind] ??= []).push(file);
    return acc;
  }, {});

  return (
    <div className="grid gap-5 lg:grid-cols-3">
      <div className="lg:col-span-2 space-y-5">
        {project.models.length > 0 && (
          <section className="card p-4">
            <h2 className="font-medium mb-3">Models</h2>
            <div className="space-y-3">
              {project.models.map((model) => (
                <div key={model.name}>
                  <p className="text-sm font-medium">{model.name}</p>
                  <ul className="mt-1 space-y-0.5">
                    {model.files.map((file) => (
                      <li key={file} className="text-xs text-slate-400 font-mono">
                        <a
                          className="hover:text-accent"
                          href={fileUrl(project.id, file)}
                          download
                        >
                          {file}
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </section>
        )}

        <section className="card p-4">
          <h2 className="font-medium mb-3">All files</h2>
          {project.files.length === 0 ? (
            <p className="text-sm text-slate-500">
              Nothing on disk yet. Drop files into{" "}
              <code className="font-mono text-slate-400">{project.slug}/models/</code>.
            </p>
          ) : (
            <div className="space-y-4">
              {Object.entries(grouped).map(([kind, files]) => (
                <div key={kind}>
                  <p className="text-xs uppercase tracking-wide text-slate-500 mb-1">{kind}</p>
                  <ul className="space-y-0.5">
                    {files.map((file) => (
                      <li key={file.rel_path} className="flex items-center gap-2 text-sm">
                        <span className="text-slate-600 w-4">{KIND_ICONS[file.kind]}</span>
                        <a
                          className="font-mono text-xs text-slate-300 hover:text-accent truncate"
                          href={fileUrl(project.id, file.rel_path)}
                          download
                        >
                          {file.rel_path}
                        </a>
                        <span className="ml-auto text-xs text-slate-600">
                          {formatSize(file.size)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      <div className="space-y-5">
        <section className="card p-4 h-fit">
          <div className="flex items-center justify-between">
            <h2 className="font-medium">Published</h2>
            {!project.makerworld_url && (
              <span className="px-2 py-0.5 rounded-full text-xs font-medium border border-slate-600 text-slate-400">
                Not published
              </span>
            )}
          </div>
          {project.makerworld_url && (
            <a
              href={project.makerworld_url}
              target="_blank"
              rel="noreferrer"
              className="text-sm text-sky-400 hover:underline break-all"
            >
              {project.makerworld_url}
            </a>
          )}
        </section>

        <DocumentsPanel project={project} />

        <section className="card p-4 h-fit">
          <h2 className="font-medium">Unfiled</h2>
        <p className="text-xs text-slate-500 mt-1 mb-3">
          On disk but not named in <code className="font-mono">project.yaml</code>. Nothing is
          absorbed silently.
        </p>
        {project.unfiled.length === 0 ? (
          <p className="text-sm text-slate-500">Everything is filed.</p>
        ) : (
          <ul className="space-y-2">
            {project.unfiled.map((file) => (
              <li key={file.rel_path} className="text-sm">
                <p className="font-mono text-xs text-amber-300 truncate">{file.rel_path}</p>
                {attaching === file.rel_path ? (
                  <form
                    className="flex gap-1 mt-1"
                    onSubmit={(e) => {
                      e.preventDefault();
                      const input = e.currentTarget.elements.namedItem(
                        "model",
                      ) as HTMLInputElement;
                      if (input.value.trim()) {
                        attach.mutate({ model: input.value.trim(), file: file.rel_path });
                      }
                    }}
                  >
                    <input
                      name="model"
                      className="input text-xs py-1"
                      autoFocus
                      placeholder="model name"
                      defaultValue={file.rel_path.split("/")[1] ?? ""}
                      list="model-names"
                    />
                    <button className="btn text-xs py-1">File</button>
                  </form>
                ) : (
                  <button
                    className="btn btn-ghost text-xs py-0.5 mt-0.5"
                    onClick={() => setAttaching(file.rel_path)}
                  >
                    File it →
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
          <datalist id="model-names">
            {project.models.map((m) => (
              <option key={m.name} value={m.name} />
            ))}
          </datalist>
        </section>
      </div>
    </div>
  );
}

const DOC_ICONS: Record<string, string> = { doc: "≡", misc: "⧉" };

/** PDFs, datasheets and anything else that belongs with the project. */
function DocumentsPanel({ project }: { project: Project }) {
  const queryClient = useQueryClient();
  const notify = useUi((s) => s.notify);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["project", project.id] });

  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      for (const file of files) await api.uploadDocument(project.id, file);
      return files.length;
    },
    onSuccess: (added) => {
      invalidate();
      notify(`Added ${count(added, "document")}`, "success");
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  const remove = useMutation({
    mutationFn: (relPath: string) => api.deleteDocument(project.id, relPath),
    onSuccess: invalidate,
    onError: (error: Error) => notify(error.message, "error"),
  });

  return (
    <section className="card p-4 h-fit">
      <h2 className="font-medium">Documents</h2>
      <p className="text-xs text-slate-500 mt-1 mb-3">
        <code className="font-mono">docs/</code> — datasheets, manuals, receipts. Kept as-is,
        never parsed.
      </p>

      <DropZone
        compact
        onFiles={(files) => upload.mutate(files)}
        busy={upload.isPending}
        accept=".pdf,.csv,.txt,.md,.rtf,.doc,.docx,.odt,.xls,.xlsx,.ods,.zip,.dxf"
        title="Drop a PDF here"
      />

      {project.documents.length > 0 && (
        <ul className="mt-3 space-y-1">
          {project.documents.map((document) => (
            <li key={document.rel_path} className="flex items-center gap-2 text-sm group">
              <span className="text-slate-600 w-4 shrink-0">
                {DOC_ICONS[document.kind] ?? "·"}
              </span>
              <a
                className="font-mono text-xs text-slate-300 hover:text-accent truncate"
                href={fileUrl(project.id, document.rel_path)}
                target="_blank"
                rel="noreferrer"
                title={document.rel_path}
              >
                {document.rel_path.slice("docs/".length)}
              </a>
              <span className="ml-auto text-xs text-slate-600 shrink-0">
                {formatSize(document.size)}
              </span>
              <button
                className="btn btn-ghost text-xs px-1 py-0 opacity-0 group-hover:opacity-100 hover:text-rose-400"
                title="Delete"
                onClick={() => remove.mutate(document.rel_path)}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function PrintsTab({ project }: { project: Project }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const notify = useUi((s) => s.notify);

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadPrint(project.id, file),
    onSuccess: (record) => {
      queryClient.invalidateQueries({ queryKey: ["project", project.id] });
      notify(`Ingested ${record.name} — ${formatDuration(record.estimated_s)}`, "success");
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  const remove = useMutation({
    mutationFn: (printId: string) => api.deletePrint(printId, true),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["project", project.id] });
      notify("Print deleted", "success");
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  return (
    <div className="space-y-4">
      <div
        className="card border-dashed p-8 text-center cursor-pointer hover:border-accent/50 transition-colors"
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          const file = e.dataTransfer.files[0];
          if (file) upload.mutate(file);
        }}
      >
        <p className="text-slate-300">
          {upload.isPending ? "Parsing…" : "Drop a sliced 3MF here"}
        </p>
        <p className="text-xs text-slate-500 mt-1">
          Settings, filament, time and the plate preview are read straight out of the file.
        </p>
        <input
          ref={inputRef}
          type="file"
          accept=".gcode.3mf,.3mf,.gcode"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) upload.mutate(file);
            e.target.value = "";
          }}
        />
      </div>

      {project.prints.length === 0 ? (
        <EmptyState title="No prints yet" hint="Slice something and drop the 3MF above." />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {project.prints.map((print) => (
            <div key={print.id} className="card p-4 space-y-2 group">
              <div className="flex items-start justify-between gap-2">
                <p className="font-medium text-sm truncate">{print.name}</p>
                <div className="flex items-center gap-2 shrink-0">
                  <StatusBadge status={print.status} />
                  <button
                    className="btn btn-ghost text-xs px-1 py-0 opacity-0 group-hover:opacity-100 hover:text-rose-400"
                    title="Delete"
                    onClick={() => remove.mutate(print.id)}
                  >
                    ✕
                  </button>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-400">
                <span>Time: {formatDuration(print.estimated_s)}</span>
                <span>Weight: {print.weight_g ?? "—"} g</span>
                <span>Layer: {print.settings.layer_height ?? "—"}</span>
                <span>Infill: {print.settings.infill_density ?? "—"}</span>
              </div>
              {print.failure_reason && (
                <p className="text-xs text-rose-300 bg-rose-500/10 rounded px-2 py-1">
                  {print.failure_reason}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ImagesTab({ project }: { project: Project }) {
  const queryClient = useQueryClient();
  const notify = useUi((s) => s.notify);
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: api.health });
  const [variant, setVariant] = useState<ImageVariant>("web");
  const [filter, setFilter] = useState<ImageVariant | "all">("all");

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["project", project.id] });

  const upload = useMutation({
    mutationFn: (files: File[]) => api.uploadAny(project.id, files, { variant }),
    onSuccess: (result) => {
      invalidate();
      result.rejected.forEach((r) => notify(`${r.filename}: ${r.error}`, "error"));
      const images = result.accepted.filter((a) => a.kind === "image").length;
      const sources = result.accepted.filter((a) => a.kind === "image_source").length;
      if (images || sources) {
        notify(
          [images && `${images} image${images > 1 ? "s" : ""} as ${variant || "untagged"}`,
           sources && `${sources} source file${sources > 1 ? "s" : ""}`]
            .filter(Boolean)
            .join(", "),
          "success",
        );
      }
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  const setCover = useMutation({
    mutationFn: (relPath: string) => api.setCover(project.id, relPath),
    onSuccess: invalidate,
  });
  const retag = useMutation({
    mutationFn: (args: { relPath: string; variant: ImageVariant }) =>
      api.setImageVariant(project.id, args.relPath, args.variant),
    onSuccess: invalidate,
    onError: (error: Error) => notify(error.message, "error"),
  });
  const remove = useMutation({
    mutationFn: (args: { relPath: string; withSource: boolean }) =>
      api.deleteImage(project.id, args.relPath, args.withSource),
    onSuccess: invalidate,
  });
  const render = useMutation({
    mutationFn: (relPath: string) => api.queueRender(project.id, relPath, 24),
    onSuccess: () => notify("Turntable queued — a minute or two on CPU"),
    onError: (error: Error) => notify(error.message, "error"),
  });

  const meshes = project.files.filter((f) => f.kind === "mesh");
  const shown =
    filter === "all" ? project.images : project.images.filter((i) => i.variant === filter);

  const counts = {
    web: project.images.filter((i) => i.variant === "web").length,
    mobile: project.images.filter((i) => i.variant === "mobile").length,
  };

  return (
    <div className="space-y-5">
      <DropZone
        onFiles={(files) => upload.mutate(files)}
        busy={upload.isPending}
        accept="image/*,.psd,.psb,.pxd,.pxm,.afphoto,.afdesign,.xcf,.ai,.svg,.sketch,.fig"
        title="Drop images and their source files here"
        hint={`Images are tagged "${variant || "untagged"}". A .psd or .pxd is filed as the editable original and paired with the image of the same name.`}
      >
        <div
          className="flex items-center justify-center gap-1 mt-3"
          onClick={(e) => e.stopPropagation()}
        >
          {(["web", "mobile", ""] as ImageVariant[]).map((option) => (
            <button
              key={option || "none"}
              onClick={() => setVariant(option)}
              className={`px-2.5 py-1 rounded-lg text-xs border transition-colors ${
                variant === option
                  ? "bg-accent text-ink-900 border-accent font-semibold"
                  : "border-ink-500 text-slate-400 hover:bg-ink-700"
              }`}
            >
              {option || "untagged"}
            </button>
          ))}
        </div>
      </DropZone>

      <div className="flex flex-wrap items-center gap-2">
        <div className="flex gap-1 mr-auto">
          {(["all", "web", "mobile"] as const).map((option) => (
            <button
              key={option}
              onClick={() => setFilter(option)}
              className={`chip transition-colors ${
                filter === option ? "bg-accent text-ink-900 border-accent" : "hover:bg-ink-600"
              }`}
            >
              {option}
              {option !== "all" && <span className="opacity-70">{counts[option]}</span>}
            </button>
          ))}
        </div>

        {meshes.length > 0 && health?.render_available && (
          <select
            className="input w-auto"
            value=""
            onChange={(e) => e.target.value && render.mutate(e.target.value)}
          >
            <option value="">Render turntable from…</option>
            {meshes.map((mesh) => (
              <option key={mesh.rel_path} value={mesh.rel_path}>
                {mesh.rel_path}
              </option>
            ))}
          </select>
        )}
        {!health?.render_available && (
          <span className="text-xs text-slate-500">
            Turntable rendering needs the optional render extras.
          </span>
        )}
      </div>

      {shown.length === 0 ? (
        <EmptyState
          title={filter === "all" ? "No images" : `No ${filter} images`}
          hint={
            filter === "all"
              ? "Ingesting a sliced 3MF gives you a plate preview for free — no compute needed."
              : `Drop one in with the "${filter}" tag selected, or retag an image below.`
          }
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {shown.map((image) => (
            <div
              key={image.rel_path}
              className={`card overflow-hidden group relative ${
                image.is_cover ? "ring-2 ring-accent" : ""
              }`}
            >
              <img
                src={thumbUrl(project.id, image.rel_path)}
                alt=""
                loading="lazy"
                className="w-full aspect-square object-cover bg-ink-900"
              />

              {image.variant && (
                <span className="absolute top-2 left-2 px-1.5 py-0.5 rounded text-xs font-medium bg-ink-900/85 border border-ink-500">
                  {image.variant}
                </span>
              )}

              <div className="p-2 space-y-2">
                <div className="flex items-center justify-between gap-1">
                  <span className="chip">{image.category}</span>
                  <div className="flex gap-1">
                    {!image.is_cover && (
                      <button
                        className="btn btn-ghost text-xs px-1.5 py-0.5"
                        title="Use as cover"
                        onClick={() => setCover.mutate(image.rel_path)}
                      >
                        ★
                      </button>
                    )}
                    <button
                      className="btn btn-ghost text-xs px-1.5 py-0.5 hover:text-rose-400"
                      title={
                        image.source_path
                          ? "Delete image (its source file is kept)"
                          : "Delete image"
                      }
                      onClick={() =>
                        remove.mutate({ relPath: image.rel_path, withSource: false })
                      }
                    >
                      ✕
                    </button>
                  </div>
                </div>

                <div className="flex gap-1">
                  {(["web", "mobile", ""] as ImageVariant[]).map((option) => (
                    <button
                      key={option || "none"}
                      title={option ? `Tag as ${option}` : "Serves both listings"}
                      onClick={() =>
                        retag.mutate({ relPath: image.rel_path, variant: option })
                      }
                      className={`flex-1 px-1 py-0.5 rounded text-xs border transition-colors ${
                        image.variant === option
                          ? "bg-ink-600 text-white border-ink-500"
                          : "border-transparent text-slate-500 hover:bg-ink-700"
                      }`}
                    >
                      {option || "both"}
                    </button>
                  ))}
                </div>

                {image.source_path ? (
                  <a
                    href={fileUrl(project.id, image.source_path)}
                    download
                    onClick={(e) => e.stopPropagation()}
                    className="flex items-center gap-1 text-xs text-slate-400 hover:text-accent truncate"
                    title={image.source_path}
                  >
                    ✎ {image.source_path.split("/").pop()}
                  </a>
                ) : (
                  <p className="text-xs text-slate-600">no source file</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SourcesTab({ project }: { project: Project }) {
  const queryClient = useQueryClient();
  const notify = useUi((s) => s.notify);
  const [editing, setEditing] = useState<{ relPath: string | null } | null>(null);
  const [pendingDelete, setPendingDelete] = useState<SourceFile | null>(null);
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: api.health });

  const { data: sources } = useQuery({
    queryKey: ["sources", project.id],
    queryFn: () => api.sources(project.id),
  });

  const upload = useMutation({
    mutationFn: (files: File[]) => api.uploadAny(project.id, files),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["project", project.id] });
      queryClient.invalidateQueries({ queryKey: ["sources", project.id] });
      result.rejected.forEach((r) => notify(`${r.filename}: ${r.error}`, "error"));
      const models = result.accepted.filter((a) => a.kind === "model_source").length;
      if (models) notify(`${models} CAD source${models > 1 ? "s" : ""} added`, "success");
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  const removeSource = useMutation({
    mutationFn: (file: SourceFile) => api.deleteModelSource(project.id, file.rel_path),
    onSuccess: (_, file) => {
      queryClient.invalidateQueries({ queryKey: ["project", project.id] });
      queryClient.invalidateQueries({ queryKey: ["sources", project.id] });
      notify(`Deleted ${file.rel_path.slice("models/sources/".length)}`, "success");
      setPendingDelete(null);
      // The deleted file may be open in the editor — close it rather than
      // leaving Save pointed at a path that no longer exists.
      setEditing((e) => (e?.relPath === file.rel_path ? null : e));
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  // The editor fetches the file's own text itself (deep link, see
  // forge-scad-editor's pages/Editor.tsx) — the host no longer needs to
  // pre-read it, so this is just picking which file the modal opens to.
  const openScad = (file: SourceFile) => setEditing({ relPath: file.rel_path });

  return (
    <div className="space-y-5">
      <DropZone
        onFiles={(files) => upload.mutate(files)}
        busy={upload.isPending}
        accept=".step,.stp,.scad,.f3d,.fcstd,.blend,.ipt,.sldprt"
        title="Drop CAD sources here"
        hint="STEP, SCAD, F3D and friends land in models/sources/. File one to a model to stop it showing as unfiled."
      />

      <div className="grid gap-5 lg:grid-cols-2">
        <SourceList
          title="Model sources"
          folder="models/sources/"
          hint="The files you actually edit. Meshes stay in their own model folders."
          project={project}
          files={sources?.models ?? []}
          // Without the editor, sources still list/download/delete — only
          // opening one to edit needs it (§4b: "there is nothing to open").
          onEditScad={health?.editor.available ? openScad : undefined}
          onDelete={setPendingDelete}
          action={
            health?.editor.available && (
              <button
                type="button"
                className="btn btn-ghost text-xs"
                onClick={() => setEditing({ relPath: null })}
              >
                + New .scad
              </button>
            )
          }
        />
        <SourceList
          title="Image sources"
          folder="images/sources/"
          hint="Photoshop, Pixelmator and the like, behind the exported images."
          project={project}
          files={sources?.images ?? []}
        />
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete file"
        message={`Delete "${pendingDelete?.rel_path.slice("models/sources/".length)}"? This removes it from disk — there is no undo.`}
        onConfirm={() => pendingDelete && removeSource.mutate(pendingDelete)}
        onCancel={() => setPendingDelete(null)}
      />

      {editing && health?.editor.available && (
        <EditorModal
          editorPath={health.editor.path ?? "/editor/"}
          projectId={project.id}
          relPath={editing.relPath}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

/**
 * Modal iframe onto the editor, deep-linked to one file — replaces the old
 * in-tree <ScadEditor> modal (§4b). `file` omitted means "new file, seeded
 * from the editor's own template"; `embed=1` tells the editor to hide its
 * own project explorer and render just the workspace, since this app
 * already knows which project/file it opened this iframe for.
 */
function EditorModal({
  editorPath,
  projectId,
  relPath,
  onClose,
}: {
  editorPath: string;
  projectId: string;
  relPath: string | null;
  onClose: () => void;
}) {
  const params = new URLSearchParams({ project: projectId, embed: "1" });
  if (relPath) params.set("file", relPath);

  return (
    <div
      className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="card w-full max-w-6xl h-[85vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-ink-600 shrink-0">
          <h2 className="font-medium">{relPath ? relPath.slice("models/sources/".length) : "New SCAD source"}</h2>
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
        </div>
        <iframe
          src={`${editorPath}?${params}`}
          title="SCAD Editor"
          className="flex-1 border-0"
        />
      </div>
    </div>
  );
}

function SourceList({
  title,
  folder,
  hint,
  project,
  files,
  onEditScad,
  onDelete,
  action,
}: {
  title: string;
  folder: string;
  hint: string;
  project: Project;
  files: SourceFile[];
  onEditScad?: (file: SourceFile) => void;
  onDelete?: (file: SourceFile) => void;
  action?: ReactNode;
}) {
  return (
    <section className="card p-4">
      <div className="flex items-center justify-between">
        <h2 className="font-medium">{title}</h2>
        {action}
      </div>
      <p className="text-xs text-slate-500 mt-1 mb-3">
        <code className="font-mono">{folder}</code> — {hint}
      </p>
      {files.length === 0 ? (
        <p className="text-sm text-slate-500">Nothing here yet.</p>
      ) : (
        <ul className="space-y-1">
          {files.map((file) => (
            <li key={file.rel_path} className="flex items-center gap-2 text-sm group">
              <a
                className="font-mono text-xs text-slate-300 hover:text-accent truncate"
                href={fileUrl(project.id, file.rel_path)}
                download
              >
                {file.rel_path.slice(folder.length)}
              </a>
              {file.filed === 0 && (
                <span className="chip text-amber-300 border-amber-500/30" title="Not named in project.yaml">
                  unfiled
                </span>
              )}
              {onEditScad && file.rel_path.toLowerCase().endsWith(".scad") && (
                <button
                  type="button"
                  className="btn btn-ghost text-xs px-1 py-0"
                  onClick={() => onEditScad(file)}
                >
                  Edit
                </button>
              )}
              <span className="ml-auto text-xs text-slate-600 shrink-0">{formatSize(file.size)}</span>
              {onDelete && (
                <button
                  type="button"
                  className="btn btn-ghost text-xs px-1 py-0 opacity-0 group-hover:opacity-100 hover:text-rose-400 shrink-0"
                  title="Delete"
                  onClick={() => onDelete(file)}
                >
                  ✕
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function VersionsTab({ project }: { project: Project }) {
  const [label, setLabel] = useState("");
  const [note, setNote] = useState("");
  const queryClient = useQueryClient();
  const notify = useUi((s) => s.notify);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["project", project.id] });

  const snapshot = useMutation({
    mutationFn: () => api.createVersion(project.id, label, note),
    onSuccess: (version) => {
      invalidate();
      notify(`Saved ${version.folder}`, "success");
      setLabel("");
      setNote("");
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  const restore = useMutation({
    mutationFn: (folder: string) => api.restoreVersion(project.id, folder),
    onSuccess: (result) => {
      invalidate();
      notify(`Restored ${result.restored}; current work saved as ${result.backup}`, "success");
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  return (
    <div className="grid gap-5 lg:grid-cols-3">
      <section className="card p-4 h-fit">
        <h2 className="font-medium">Save a version</h2>
        <p className="text-xs text-slate-500 mt-1 mb-3">
          Copies <code className="font-mono">models/</code> into a dated folder you can open in
          any file manager.
        </p>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            snapshot.mutate();
          }}
        >
          <div>
            <label className="label">Label</label>
            <input
              className="input"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="reinforced-hinge"
            />
          </div>
          <div>
            <label className="label">Note</label>
            <textarea
              className="input"
              rows={3}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="What changed and why"
            />
          </div>
          <button className="btn btn-primary w-full" disabled={snapshot.isPending}>
            {snapshot.isPending ? "Saving…" : "Save version"}
          </button>
        </form>
      </section>

      <div className="lg:col-span-2 space-y-3">
        {project.versions.length === 0 ? (
          <EmptyState title="No versions yet" hint="Snapshot before you make a risky change." />
        ) : (
          project.versions.map((version) => (
            <div key={version.folder} className="card p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-medium text-sm">
                    v{String(version.number).padStart(3, "0")}
                    {version.label && (
                      <span className="text-slate-400 font-normal"> · {version.label}</span>
                    )}
                  </p>
                  <p className="text-xs text-slate-500 font-mono mt-0.5 truncate">
                    _versions/{version.folder}
                  </p>
                  {version.note && (
                    <p className="text-sm text-slate-400 mt-2">{version.note}</p>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-xs text-slate-600">{version.file_count} files</span>
                  <button
                    className="btn text-xs"
                    onClick={() => restore.mutate(version.folder)}
                    title="Restores into models/, snapshotting current work first"
                  >
                    Restore
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function NotesTab({ project }: { project: Project }) {
  const [notes, setNotes] = useState(project.notes);
  const queryClient = useQueryClient();
  const notify = useUi((s) => s.notify);

  const save = useMutation({
    mutationFn: () => api.updateProject(project.id, { notes }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["project", project.id] });
      notify("Saved to notes.md", "success");
    },
  });

  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="font-medium">
          Notes <span className="text-xs text-slate-500 font-mono ml-2">notes.md</span>
        </h2>
        <button
          className="btn btn-primary"
          disabled={notes === project.notes || save.isPending}
          onClick={() => save.mutate()}
        >
          Save
        </button>
      </div>
      <textarea
        className="input font-mono text-sm"
        rows={20}
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="# Notes&#10;&#10;What you tried, what worked."
      />
    </div>
  );
}
