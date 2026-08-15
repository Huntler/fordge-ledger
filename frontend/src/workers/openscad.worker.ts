// @ts-nocheck -- worker global scope isn't in this project's DOM-only tsconfig lib;
// the typed boundary lives in ../lib/scadRenderer.ts, which every consumer imports instead.

/**
 * Compiles OpenSCAD source to STL entirely client-side, off the main thread.
 * OpenSCAD itself is a real Emscripten build (~9MB WASM) fetched at
 * dev/build time by ../../scripts/fetch-openscad-wasm.mjs into
 * public/openscad/ — see that script for where it comes from and why (it's
 * openscad.org's own official build, with the Manifold backend actually
 * compiled in, unlike the openscad-wasm@0.0.4 npm package this replaced).
 * This file is the only place that touches it, and openscad.js/.wasm are
 * only fetched over the network when a .scad editor panel is actually
 * opened (see scadRenderer.ts).
 *
 * A fresh module instance is created for every render and discarded right
 * after. That looks wasteful, but reusing one instance across calls is
 * unreliable in this build too: verified by hand — a *second* callMain() on
 * the same instance reliably fails with an opaque native exception ("program
 * has already aborted!"), even re-rendering the exact same plain,
 * non-boolean cube twice in a row. It is not specific to CSG booleans;
 * something in the module's internal geometry cache does not survive being
 * reused. A fresh instance per call sidesteps it entirely, at the cost of
 * paying WASM instantiation again each time — see README.md § "The
 * in-browser SCAD editor".
 *
 * openscad.js lives in public/openscad/ rather than in the source tree, so
 * it can't be a static `import` — Vite refuses to resolve an import into
 * public/ through its module graph (dev-time error) or bundle one
 * (build-time error). Fetching its text and importing it from a Blob URL
 * sidesteps both: a Blob URL isn't a specifier Vite can recognize, so it
 * passes the import through untouched, in dev and in the production build
 * alike.
 */
async function loadOpenSCAD() {
  const source = await fetch("/openscad/openscad.js").then((res) => res.text());
  const blobUrl = URL.createObjectURL(new Blob([source], { type: "text/javascript" }));
  try {
    return (await import(/* @vite-ignore */ blobUrl)).default;
  } finally {
    URL.revokeObjectURL(blobUrl);
  }
}

async function createInstance() {
  const OpenSCAD = await loadOpenSCAD();
  return OpenSCAD({
    noInitialRun: true,
    locateFile: (path: string) => `/openscad/${path}`,
    // Worker console output isn't captured by the devtools bridge used
    // during development, so relay OpenSCAD's own stdout/stderr to the
    // main thread as regular messages instead of calling console here.
    print: (text: string) => self.postMessage({ log: "out", text }),
    printErr: (text: string) => self.postMessage({ log: "err", text }),
  });
}

// OpenSCAD's own defaults ($fa=12, $fs=2, $fn=0) produce very coarse
// cylinders/spheres — a 2mm-radius cylinder gets ~6 facets. These presets
// set $fn as a command-line default (overridable by the script itself, same
// as OpenSCAD's customizer variables) so curved geometry looks reasonable
// without editing the source. Keep in sync with the dropdown in
// ScadWorkspace.tsx.
const FN_BY_QUALITY = { low: 16, medium: 64, high: 128 };

self.onmessage = async (event) => {
  const { id, code, quality } = event.data;
  try {
    const instance = await createInstance();
    instance.FS.writeFile("/input.scad", code);
    const fn = FN_BY_QUALITY[quality] ?? FN_BY_QUALITY.medium;
    instance.callMain([
      "/input.scad",
      // Manifold moved from an experimental --enable flag to its own
      // top-level option on modern OpenSCAD; --enable=manifold is silently
      // ignored (falls back to the much slower default CGAL kernel) and was
      // the reason renders used to take forever. --export-format pins the
      // STL as ASCII text, matching the encoding: "utf8" read below —
      // OpenSCAD's own --help notes binary is planned as a future default.
      "--backend=Manifold",
      "--export-format=asciistl",
      "-D",
      `$fn=${fn}`,
      "-o",
      "/output.stl",
    ]);
    const stl = instance.FS.readFile("/output.stl", { encoding: "utf8" });
    self.postMessage({ id, ok: true, stl });
  } catch (err) {
    self.postMessage({ id, ok: false, error: err instanceof Error ? err.message : String(err) });
  }
};
