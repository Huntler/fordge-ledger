/**
 * Starter content for a brand-new .scad source. Deliberately its own tiny
 * module (no CodeMirror/three.js/openscad-wasm deps) — Project.tsx and the
 * Editor page's file explorer both need this constant, but neither should
 * have to eagerly pull in ScadWorkspace's heavy, lazy-loaded bundle just to
 * read one string.
 */
export const NEW_SCAD_TEMPLATE = "// New OpenSCAD source\ncube([20, 20, 20]);\n";
