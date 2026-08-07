// @ts-nocheck -- worker global scope isn't in this project's DOM-only tsconfig lib;
// the typed boundary lives in ../lib/scadRenderer.ts, which every consumer imports instead.

/**
 * Compiles OpenSCAD source to STL entirely client-side, off the main thread.
 * openscad-wasm is a real Emscripten build of OpenSCAD (~14MB, single-file,
 * base64-inlined WASM) — this file is the only place that touches it, and it's
 * only fetched when a .scad editor panel is actually opened (see scadRenderer.ts).
 *
 * A fresh module instance is created for every render and discarded right
 * after. That looks wasteful, but reusing one instance across calls is
 * unreliable in this build: verified by hand — a *second* callMain() on the
 * same instance reliably fails with an opaque native exception (a stringified
 * pointer, e.g. "1124712", not a real message), even re-rendering the exact
 * same plain, non-boolean cube twice in a row. It is not specific to CSG
 * booleans; something in the module's internal geometry cache does not
 * survive being reused. A fresh instance per call sidesteps it entirely, at
 * the cost of paying WASM instantiation again each time — see
 * README.md § "The in-browser SCAD editor".
 */
import { createOpenSCAD } from "openscad-wasm";

function createInstance() {
  return createOpenSCAD({
    noInitialRun: true,
    // Worker console output isn't captured by the devtools bridge used
    // during development, so relay OpenSCAD's own stdout/stderr to the
    // main thread as regular messages instead of calling console here.
    print: (text) => self.postMessage({ log: "out", text }),
    printErr: (text) => self.postMessage({ log: "err", text }),
  }).then((wrapper) => wrapper.getInstance());
}

self.onmessage = async (event) => {
  const { id, code } = event.data;
  try {
    const instance = await createInstance();
    instance.FS.writeFile("/input.scad", code);
    instance.callMain(["/input.scad", "--enable=manifold", "-o", "/output.stl"]);
    const stl = instance.FS.readFile("/output.stl", { encoding: "utf8" });
    self.postMessage({ id, ok: true, stl });
  } catch (err) {
    self.postMessage({ id, ok: false, error: err instanceof Error ? err.message : String(err) });
  }
};
