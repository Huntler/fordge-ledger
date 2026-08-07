/** Typed client for the FastAPI backend. */

export type ProjectStatus =
  | "idea"
  | "designing"
  | "testing"
  | "ready"
  | "published"
  | "shelved";

export type PrintStatus = "queued" | "printing" | "done" | "failed";

export interface RemixSource {
  url: string;
  title?: string;
  author?: string;
  license?: string;
}

export interface ProjectSummary {
  id: string;
  slug: string;
  title: string;
  status: ProjectStatus;
  created: string;
  tags: string[];
  license: string;
  notes: string;
  cover_image: string;
  makerworld_url: string | null;
  model_count: number;
  print_count: number;
  unfiled_count: number;
}

export interface ProjectFile {
  rel_path: string;
  kind: "cad" | "mesh" | "sliced" | "image" | "image_source" | "doc" | "misc" | "other";
  size: number;
  mtime: number;
  filed: number;
}

export interface ModelEntry {
  name: string;
  files: string[];
}

/** Which listing an image is cut for. Empty means it serves both. */
export type ImageVariant = "" | "web" | "mobile";

export interface ProjectImage {
  rel_path: string;
  category: "photo" | "render" | "plate" | "other";
  sort_order: number;
  is_cover: boolean;
  variant: ImageVariant;
  /** Editable original it was exported from, e.g. images/sources/cover.psd. */
  source_path: string;
}

export interface ProjectDocument {
  rel_path: string;
  kind: string;
  size: number;
  mtime: number;
}

export interface SourceFile {
  rel_path: string;
  kind: string;
  size: number;
  mtime: number;
  filed: number;
}

export interface ProjectSources {
  images: SourceFile[];
  models: SourceFile[];
}

export interface UploadResult {
  accepted: {
    kind: "image" | "image_source" | "model_source" | "document" | "print";
    rel_path: string;
    variant?: string;
    linked_to?: string;
  }[];
  rejected: { filename: string; error: string }[];
}

export interface VersionEntry {
  number: number;
  folder: string;
  label: string;
  note: string;
  created: string;
  file_count: number;
}

export interface Filament {
  slot: number | null;
  type: string | null;
  color: string | null;
  used_m: number | null;
  used_g: number | null;
}

export interface PrintJob {
  id: string;
  project_id: string;
  project_title?: string;
  project_slug?: string;
  rel_path: string;
  name: string;
  model_name: string | null;
  status: PrintStatus;
  printer: string | null;
  nozzle: number | null;
  filaments: Filament[];
  estimated_s: number | null;
  actual_s: number | null;
  weight_g: number | null;
  cost: number | null;
  notes: string;
  failure_reason: string | null;
  failure_fix: string | null;
  settings: Record<string, string>;
  created: string;
  started: string | null;
  finished: string | null;
}

export interface Project extends ProjectSummary {
  remix_of: RemixSource[];
  makerworld_url: string | null;
  models: ModelEntry[];
  files: ProjectFile[];
  unfiled: { rel_path: string; kind: string; size: number }[];
  images: ProjectImage[];
  documents: ProjectDocument[];
  versions: VersionEntry[];
  prints: PrintJob[];
}

export interface PublishDraft {
  title: string;
  summary: string;
  description: string;
  tags: string[];
  category: string;
  license: string;
  template: string;
  print_ids: string[];
  assets: string[];
  checklist: Record<string, boolean>;
  profiles: PrintProfile[];
  context: Record<string, string>;
  exported_at?: string | null;
}

export interface PrintProfile {
  name: string;
  printer: string | null;
  nozzle: number | null;
  layer_height?: string;
  walls?: string;
  infill?: string;
  infill_pattern?: string;
  filament: string;
  time: string;
  weight_g: number | null;
}

export interface MarkdownDoc {
  name: string;
  body: string;
}

export interface Health {
  status: string;
  library_path: string;
  data_path: string;
  counts: Record<string, number>;
  last_full_scan: string | null;
  watcher: boolean;
  render_available: boolean;
  llm_configured: boolean;
  llm_provider: string;
  reflink_available: boolean;
}

export type LlmProvider = "ollama" | "lmstudio";

export interface LlmStatus {
  available: boolean;
  reason?: string;
  /** Actionable follow-up when the connection failed. */
  hint?: string;
  provider?: LlmProvider;
  base_url?: string;
  model?: string;
  models: string[];
  model_present?: boolean;
}

export type PolishStatus = "running" | "done" | "failed" | "cancelled";

export interface PolishRun {
  id: string;
  status: PolishStatus;
  elapsed_seconds: number;
  original?: string;
  polished?: string;
  error?: string;
}

export interface LlmSettings {
  provider: LlmProvider;
  base_url: string;
  model: string;
  system_prompt: string;
  temperature: number;
  timeout_seconds: number;
  providers: LlmProvider[];
  default_system_prompt: string;
  settings_path: string;
}

export interface Job {
  id: number;
  kind: string;
  status: "pending" | "running" | "done" | "failed" | "cancelled";
  progress: number;
  message: string;
  error: string | null;
}

export interface PrintStats {
  counts: Record<PrintStatus, number>;
  filament_g: number;
  filament_cost: number;
  print_seconds: number;
  success_rate: number | null;
}

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers:
      init?.body instanceof FormData
        ? init?.headers
        : { "Content-Type": "application/json", ...init?.headers },
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      // FastAPI puts validation errors in a list; flatten them to one line.
      detail = Array.isArray(body.detail)
        ? body.detail.map((d: { msg: string }) => d.msg).join(", ")
        : (body.detail ?? detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail, response.status);
  }

  return response.status === 204 ? (undefined as T) : response.json();
}

const json = (body: unknown) => JSON.stringify(body);

export const api = {
  health: () => request<Health>("/api/health"),
  stats: () => request<{ projects_by_status: Record<string, number>; prints: PrintStats }>(
    "/api/stats",
  ),
  rescan: () => request<{ job_id: number }>("/api/rescan", { method: "POST" }),
  jobs: () => request<Job[]>("/api/jobs?limit=20"),

  projects: (params: { q?: string; status?: string; tag?: string; sort?: string } = {}) => {
    const search = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v) as [string, string][],
    );
    return request<ProjectSummary[]>(`/api/projects?${search}`);
  },
  tags: () => request<{ tag: string; count: number }[]>("/api/projects/tags"),
  project: (id: string) => request<Project>(`/api/projects/${id}`),
  createProject: (body: { title: string; tags?: string[]; license?: string }) =>
    request<Project>("/api/projects", { method: "POST", body: json(body) }),
  updateProject: (id: string, body: Record<string, unknown>) =>
    request<Project>(`/api/projects/${id}`, { method: "PATCH", body: json(body) }),
  deleteProject: (id: string) =>
    request<{ id: string; title: string; purged: boolean; trashed_to: string | null }>(
      `/api/projects/${id}`,
      { method: "DELETE" },
    ),
  rescanProject: (id: string) =>
    request<{ ok: boolean }>(`/api/projects/${id}/rescan`, { method: "POST" }),
  attachFiles: (id: string, modelName: string, files: string[]) =>
    request<Project>(`/api/projects/${id}/attach`, {
      method: "POST",
      body: json({ model_name: modelName, files }),
    }),

  prints: (params: { project_id?: string; status?: string } = {}) => {
    const search = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v) as [string, string][],
    );
    return request<PrintJob[]>(`/api/prints?${search}`);
  },
  printStats: () => request<PrintStats>("/api/prints/stats"),
  failures: () => request<PrintJob[]>("/api/prints/failures"),
  updatePrint: (id: string, body: Record<string, unknown>) =>
    request<PrintJob>(`/api/prints/${id}`, { method: "PATCH", body: json(body) }),
  deletePrint: (id: string, removeFiles = false) =>
    request<void>(`/api/prints/${id}?remove_files=${removeFiles}`, { method: "DELETE" }),
  uploadPrint: (projectId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<PrintJob>(`/api/projects/${projectId}/prints`, {
      method: "POST",
      body: form,
    });
  },
  ingestPrints: (projectId: string) =>
    request<{ job_id: number }>(`/api/projects/${projectId}/prints/ingest`, { method: "POST" }),

  images: (projectId: string) => request<ProjectImage[]>(`/api/projects/${projectId}/images`),
  sources: (projectId: string) => request<ProjectSources>(`/api/projects/${projectId}/sources`),

  uploadModelSource: (projectId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ rel_path: string }>(`/api/projects/${projectId}/models/sources`, {
      method: "POST",
      body: form,
    });
  },
  /** Save from the in-browser SCAD editor — overwrites an existing source in place. */
  writeModelSource: (projectId: string, relPath: string, text: string) =>
    request<void>(
      `/api/projects/${projectId}/models/sources/content?rel_path=${encodeURIComponent(relPath)}`,
      { method: "PUT", headers: { "Content-Type": "text/plain" }, body: text },
    ),
  /** Compiled STL next to its .scad source, same name — always overwrites. */
  exportModelStl: (projectId: string, relPath: string, stl: string) =>
    request<{ rel_path: string }>(
      `/api/projects/${projectId}/models/sources/export?rel_path=${encodeURIComponent(relPath)}`,
      { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: stl },
    ),
  deleteModelSource: (projectId: string, relPath: string) =>
    request<void>(
      `/api/projects/${projectId}/models/sources?rel_path=${encodeURIComponent(relPath)}`,
      { method: "DELETE" },
    ),

  documents: (projectId: string) =>
    request<ProjectDocument[]>(`/api/projects/${projectId}/documents`),
  uploadDocument: (projectId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ rel_path: string }>(`/api/projects/${projectId}/documents`, {
      method: "POST",
      body: form,
    });
  },
  deleteDocument: (projectId: string, relPath: string) =>
    request<void>(
      `/api/projects/${projectId}/documents?rel_path=${encodeURIComponent(relPath)}`,
      { method: "DELETE" },
    ),

  uploadImage: (projectId: string, file: File, variant: ImageVariant = "") => {
    const form = new FormData();
    form.append("file", file);
    form.append("variant", variant);
    return request<{ rel_path: string }>(`/api/projects/${projectId}/images`, {
      method: "POST",
      body: form,
    });
  },

  /** One drop zone for everything; the server files each by extension. */
  uploadAny: (
    projectId: string,
    files: File[],
    opts: { variant?: ImageVariant; forImage?: string } = {},
  ) => {
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    form.append("variant", opts.variant ?? "");
    form.append("for_image", opts.forImage ?? "");
    return request<UploadResult>(`/api/projects/${projectId}/upload`, {
      method: "POST",
      body: form,
    });
  },

  uploadImageSource: (projectId: string, file: File, forImage: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("for_image", forImage);
    return request<{ rel_path: string }>(`/api/projects/${projectId}/images/sources`, {
      method: "POST",
      body: form,
    });
  },

  setImageVariant: (projectId: string, relPath: string, variant: ImageVariant) =>
    request<void>(`/api/projects/${projectId}/images/variant`, {
      method: "PUT",
      body: json({ rel_path: relPath, variant }),
    }),
  setCover: (projectId: string, relPath: string) =>
    request<void>(`/api/projects/${projectId}/images/cover`, {
      method: "PUT",
      body: json({ rel_path: relPath }),
    }),
  reorderImages: (projectId: string, relPaths: string[]) =>
    request<void>(`/api/projects/${projectId}/images/order`, {
      method: "PUT",
      body: json({ rel_paths: relPaths }),
    }),
  deleteImage: (projectId: string, relPath: string, withSource = false) =>
    request<void>(
      `/api/projects/${projectId}/images?rel_path=${encodeURIComponent(relPath)}` +
        `&with_source=${withSource}`,
      { method: "DELETE" },
    ),
  queueRender: (projectId: string, relPath: string, frames: number) =>
    request<{ job_id: number }>(`/api/projects/${projectId}/render`, {
      method: "POST",
      body: json({ rel_path: relPath, frames }),
    }),

  versions: (projectId: string) => request<VersionEntry[]>(`/api/projects/${projectId}/versions`),
  createVersion: (projectId: string, label: string, note: string) =>
    request<VersionEntry>(`/api/projects/${projectId}/versions`, {
      method: "POST",
      body: json({ label, note }),
    }),
  restoreVersion: (projectId: string, folder: string) =>
    request<{ restored: string; backup: string }>(
      `/api/projects/${projectId}/versions/${encodeURIComponent(folder)}/restore`,
      { method: "POST" },
    ),
  deleteVersion: (projectId: string, folder: string) =>
    request<void>(`/api/projects/${projectId}/versions/${encodeURIComponent(folder)}`, {
      method: "DELETE",
    }),

  templates: () => request<MarkdownDoc[]>("/api/templates"),
  saveTemplate: (doc: MarkdownDoc) =>
    request<MarkdownDoc>("/api/templates", { method: "PUT", body: json(doc) }),
  snippets: () => request<MarkdownDoc[]>("/api/snippets"),
  saveSnippet: (doc: MarkdownDoc) =>
    request<MarkdownDoc>("/api/snippets", { method: "PUT", body: json(doc) }),

  publishDraft: (projectId: string) =>
    request<PublishDraft>(`/api/projects/${projectId}/publish`),
  savePublishDraft: (projectId: string, body: Record<string, unknown>) =>
    request<PublishDraft>(`/api/projects/${projectId}/publish`, {
      method: "PUT",
      body: json(body),
    }),
  publishPreview: (projectId: string, template: string | null, printIds: string[]) =>
    request<{ markdown: string }>(`/api/projects/${projectId}/publish/preview`, {
      method: "POST",
      body: json({ template, print_ids: printIds }),
    }),
  publishExport: (projectId: string) =>
    request<{ path: string; assets: string[] }>(
      `/api/projects/${projectId}/publish/export`,
      { method: "POST" },
    ),

  llmStatus: () => request<LlmStatus>("/api/llm/status"),

  /** Starts a run and returns its id; the model keeps working server-side. */
  startPolish: (text: string, instructions = "") =>
    request<PolishRun>("/api/llm/polish", {
      method: "POST",
      body: json({ text, instructions }),
    }),
  pollPolish: (runId: string) => request<PolishRun>(`/api/llm/polish/${runId}`),
  cancelPolish: (runId: string) =>
    request<PolishRun>(`/api/llm/polish/${runId}/cancel`, { method: "POST" }),

  llmSettings: () => request<LlmSettings>("/api/settings/llm"),
  saveLlmSettings: (body: Partial<LlmSettings>) =>
    request<LlmSettings>("/api/settings/llm", { method: "PUT", body: json(body) }),
  /** Probe without saving. The backend does the reaching out, not the browser. */
  testLlmSettings: (body: Partial<LlmSettings>) =>
    request<LlmStatus>("/api/settings/llm/test", { method: "POST", body: json(body) }),
  reloadLlmSettings: () =>
    request<LlmSettings>("/api/settings/llm/reload", { method: "POST" }),

};

export const fileUrl = (projectId: string, relPath: string) =>
  `/api/projects/${projectId}/file?rel_path=${encodeURIComponent(relPath)}`;

export const thumbUrl = (projectId: string, relPath: string) =>
  `/api/projects/${projectId}/thumb?rel_path=${encodeURIComponent(relPath)}`;

/** Raw text content of a project file — for loading a .scad source into the editor. */
export async function readTextFile(projectId: string, relPath: string): Promise<string> {
  const response = await fetch(fileUrl(projectId, relPath));
  if (!response.ok) throw new ApiError(`Could not load ${relPath}`, response.status);
  return response.text();
}
