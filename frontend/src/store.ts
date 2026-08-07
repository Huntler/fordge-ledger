import { create } from "zustand";

export interface Toast {
  id: number;
  message: string;
  tone: "info" | "error" | "success";
}

interface UiState {
  toasts: Toast[];
  /** Live job progress keyed by job id, fed by the SSE stream. */
  jobs: Record<number, { kind: string; progress: number; message: string }>;
  notify: (message: string, tone?: Toast["tone"]) => void;
  dismiss: (id: number) => void;
  setJob: (id: number, kind: string, progress: number, message: string) => void;
  clearJob: (id: number) => void;
}

let nextToastId = 1;

export const useUi = create<UiState>((set) => ({
  toasts: [],
  jobs: {},

  notify: (message, tone = "info") => {
    const id = nextToastId++;
    set((state) => ({ toasts: [...state.toasts, { id, message, tone }] }));
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), 5000);
  },

  dismiss: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),

  setJob: (id, kind, progress, message) =>
    set((state) => ({ jobs: { ...state.jobs, [id]: { kind, progress, message } } })),

  clearJob: (id) =>
    set((state) => {
      const jobs = { ...state.jobs };
      delete jobs[id];
      return { jobs };
    }),
}));
