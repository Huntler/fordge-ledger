import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, thumbUrl, type ProjectSummary } from "../api";
import { EmptyState, Modal, Spinner, StatusBadge } from "../components/ui";
import { useUi } from "../store";

export default function Library() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [tag, setTag] = useState("");
  const [creating, setCreating] = useState(false);

  const { data: projects, isLoading } = useQuery({
    queryKey: ["projects", { q, status, tag }],
    queryFn: () => api.projects({ q, status, tag }),
  });
  const { data: tags } = useQuery({ queryKey: ["tags"], queryFn: api.tags });
  const { data: statuses } = useQuery({
    queryKey: ["statuses"],
    queryFn: async () => (await fetch("/api/projects/statuses")).json() as Promise<string[]>,
  });

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold mr-auto">Library</h1>
        <input
          className="input max-w-xs"
          placeholder="Search titles and notes…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select className="input max-w-[10rem]" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Any status</option>
          {statuses?.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <button className="btn btn-primary" onClick={() => setCreating(true)}>
          New project
        </button>
      </div>

      {tags && tags.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {tags.map((t) => (
            <button
              key={t.tag}
              onClick={() => setTag(tag === t.tag ? "" : t.tag)}
              className={`chip transition-colors ${
                tag === t.tag ? "bg-accent text-ink-900 border-accent" : "hover:bg-ink-600"
              }`}
            >
              {t.tag}
              <span className="opacity-60">{t.count}</span>
            </button>
          ))}
        </div>
      )}

      {isLoading ? (
        <Spinner />
      ) : projects && projects.length > 0 ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {projects.map((project) => (
            <ProjectCard key={project.id} project={project} />
          ))}
        </div>
      ) : (
        <EmptyState
          title="Nothing here yet"
          hint="Create a project, or drop a folder straight into the library directory — the watcher will pick it up."
          action={
            <button className="btn btn-primary" onClick={() => setCreating(true)}>
              New project
            </button>
          }
        />
      )}

      <NewProjectModal open={creating} onClose={() => setCreating(false)} />
    </div>
  );
}

function ProjectCard({ project }: { project: ProjectSummary }) {
  return (
    <Link
      to={`/projects/${project.id}`}
      className="card overflow-hidden hover:border-ink-500 transition-colors group"
    >
      <div className="aspect-[4/3] bg-ink-900 flex items-center justify-center overflow-hidden">
        {project.cover_image ? (
          <img
            src={thumbUrl(project.id, project.cover_image)}
            alt=""
            loading="lazy"
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
          />
        ) : (
          <span className="text-4xl text-ink-500">⬢</span>
        )}
      </div>

      <div className="p-3 space-y-2">
        <div className="flex items-start justify-between gap-2">
          <h3 className="font-medium leading-tight">{project.title}</h3>
          <StatusBadge status={project.status} />
        </div>

        <div className="flex flex-wrap gap-1">
          {project.tags.slice(0, 3).map((tag) => (
            <span key={tag} className="chip">
              {tag}
            </span>
          ))}
        </div>

        <div className="flex gap-3 text-xs text-slate-500">
          <span>{project.model_count} models</span>
          <span>{project.print_count} prints</span>
          {project.unfiled_count > 0 && (
            <span className="text-amber-400" title="Files on disk not listed in project.yaml">
              {project.unfiled_count} unfiled
            </span>
          )}
        </div>
      </div>
    </Link>
  );
}

function NewProjectModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [title, setTitle] = useState("");
  const [tags, setTags] = useState("");
  const queryClient = useQueryClient();
  const notify = useUi((s) => s.notify);

  const create = useMutation({
    mutationFn: () =>
      api.createProject({
        title,
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
      }),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      notify(`Created ${project.title}`, "success");
      setTitle("");
      setTags("");
      onClose();
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  return (
    <Modal open={open} title="New project" onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (title.trim()) create.mutate();
        }}
      >
        <div>
          <label className="label">Title</label>
          <input
            className="input"
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Desk Organizer"
          />
          <p className="text-xs text-slate-500 mt-1">
            The folder is named from this, and you can rename it by hand later.
          </p>
        </div>

        <div>
          <label className="label">Tags</label>
          <input
            className="input"
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            placeholder="office, storage, parametric"
          />
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={!title.trim() || create.isPending}>
            {create.isPending ? "Creating…" : "Create"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
