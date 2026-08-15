/**
 * Main-thread handle onto the OpenSCAD-in-a-worker pipeline. One instance per
 * open editor panel — the worker (and its ~14MB wasm module) is created lazily
 * on first render() and torn down when the panel closes.
 */

export type RenderQuality = "low" | "medium" | "high";

type WorkerResponse =
  | { id: number; ok: true; stl: string }
  | { id: number; ok: false; error: string }
  | { log: "out" | "err"; text: string };

export class ScadRenderError extends Error {}

export class ScadRenderer {
  private worker: Worker | null = null;
  private nextId = 1;
  private pending = new Map<number, { resolve: (stl: string) => void; reject: (err: Error) => void }>();

  private ensureWorker(): Worker {
    if (!this.worker) {
      const worker = new Worker(new URL("../workers/openscad.worker.ts", import.meta.url), {
        type: "module",
      });
      worker.onmessage = (event: MessageEvent<WorkerResponse>) => {
        const msg = event.data;
        if ("log" in msg) {
          (msg.log === "err" ? console.error : console.log)("[openscad]", msg.text);
          return;
        }
        const entry = this.pending.get(msg.id);
        if (!entry) return;
        this.pending.delete(msg.id);
        if (msg.ok) entry.resolve(msg.stl);
        else entry.reject(new ScadRenderError(msg.error));
      };
      worker.onerror = (event) => {
        // A wasm-level crash (out of memory, etc.) — nothing to correlate by
        // id, so fail every render still in flight and let the caller retry,
        // which lazily spins up a fresh worker.
        const err = new ScadRenderError(event.message || "OpenSCAD worker crashed");
        this.pending.forEach(({ reject }) => reject(err));
        this.pending.clear();
        worker.terminate();
        this.worker = null;
      };
      this.worker = worker;
    }
    return this.worker;
  }

  /**
   * `files` are extra virtual-FS entries the code may `use`/`include` —
   * currently just referenced tool snippets, keyed by their path
   * (`tools/<slug>.scad`), written in before compiling. See
   * openscad.worker.ts.
   */
  render(
    code: string,
    quality: RenderQuality = "medium",
    files: Record<string, string> = {},
  ): Promise<string> {
    const worker = this.ensureWorker();
    const id = this.nextId++;
    return new Promise<string>((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      worker.postMessage({ id, code, quality, files });
    });
  }

  terminate() {
    this.worker?.terminate();
    this.worker = null;
    this.pending.clear();
  }
}
