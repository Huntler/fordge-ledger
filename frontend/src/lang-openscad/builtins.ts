/**
 * OpenSCAD's builtin modules, functions, special variables, and structural
 * keywords — the static half of autocomplete (see completions.ts for the
 * dynamic, per-file half) and the "known name" set the linter checks calls
 * and `$`-variables against (see lint.ts).
 */

export type BuiltinKind = "module" | "function" | "special-variable" | "keyword" | "constant";

export interface BuiltinEntry {
  label: string;
  kind: BuiltinKind;
  /** Short signature shown inline in the completion popup, e.g. "cube(size, center=false)". */
  detail: string;
  /** CodeMirror snippet template (${}-placeholders) for structural/common entries; plain-inserted otherwise. */
  snippet?: string;
}

export const BUILTIN_MODULES: BuiltinEntry[] = [
  // 3D primitives
  { label: "cube", kind: "module", detail: "cube(size, center=false)", snippet: "cube([${1:size}, ${2:size}, ${3:size}]);" },
  { label: "sphere", kind: "module", detail: "sphere(r|d, $fn)", snippet: "sphere(r=${1:1});" },
  { label: "cylinder", kind: "module", detail: "cylinder(h, r1, r2, center=false)", snippet: "cylinder(h=${1:1}, r=${2:1}, center=${3:false});" },
  { label: "polyhedron", kind: "module", detail: "polyhedron(points, faces)" },
  // 2D primitives
  { label: "circle", kind: "module", detail: "circle(r|d, $fn)", snippet: "circle(r=${1:1});" },
  { label: "square", kind: "module", detail: "square(size, center=false)", snippet: "square([${1:size}, ${2:size}]);" },
  { label: "polygon", kind: "module", detail: "polygon(points, paths)" },
  { label: "text", kind: "module", detail: "text(text, size, font, halign, valign)" },
  { label: "import", kind: "module", detail: "import(file, convexity)" },
  { label: "surface", kind: "module", detail: "surface(file, center, invert)" },
  { label: "projection", kind: "module", detail: "projection(cut=false)" },
  // Transformations
  { label: "translate", kind: "module", detail: "translate(v)", snippet: "translate([${1:x}, ${2:y}, ${3:z}])\n  ${4}" },
  { label: "rotate", kind: "module", detail: "rotate(a, v)", snippet: "rotate([${1:x}, ${2:y}, ${3:z}])\n  ${4}" },
  { label: "scale", kind: "module", detail: "scale(v)", snippet: "scale([${1:x}, ${2:y}, ${3:z}])\n  ${4}" },
  { label: "resize", kind: "module", detail: "resize(newsize, auto=false)" },
  { label: "mirror", kind: "module", detail: "mirror(v)" },
  { label: "multmatrix", kind: "module", detail: "multmatrix(m)" },
  { label: "color", kind: "module", detail: "color(c, alpha=1)", snippet: 'color("${1:red}")\n  ${2}' },
  { label: "offset", kind: "module", detail: "offset(r|delta, chamfer=false)" },
  { label: "hull", kind: "module", detail: "hull()", snippet: "hull() {\n  ${1}\n}" },
  { label: "minkowski", kind: "module", detail: "minkowski()", snippet: "minkowski() {\n  ${1}\n}" },
  // Boolean ops
  { label: "union", kind: "module", detail: "union()", snippet: "union() {\n  ${1}\n}" },
  { label: "difference", kind: "module", detail: "difference()", snippet: "difference() {\n  ${1}\n}" },
  { label: "intersection", kind: "module", detail: "intersection()", snippet: "intersection() {\n  ${1}\n}" },
  // Extrusion
  { label: "linear_extrude", kind: "module", detail: "linear_extrude(height, center, twist)", snippet: "linear_extrude(height=${1:1})\n  ${2}" },
  { label: "rotate_extrude", kind: "module", detail: "rotate_extrude(angle=360)", snippet: "rotate_extrude()\n  ${1}" },
  // Misc
  { label: "render", kind: "module", detail: "render(convexity)" },
  { label: "children", kind: "module", detail: "children(index)" },
  { label: "echo", kind: "module", detail: "echo(values...)", snippet: "echo(${1});" },
  { label: "assert", kind: "module", detail: "assert(condition, message)", snippet: "assert(${1:condition});" },
];

export const BUILTIN_FUNCTIONS: BuiltinEntry[] = [
  { label: "abs", kind: "function", detail: "abs(x)" },
  { label: "sign", kind: "function", detail: "sign(x)" },
  { label: "sin", kind: "function", detail: "sin(deg)" },
  { label: "cos", kind: "function", detail: "cos(deg)" },
  { label: "tan", kind: "function", detail: "tan(deg)" },
  { label: "asin", kind: "function", detail: "asin(x)" },
  { label: "acos", kind: "function", detail: "acos(x)" },
  { label: "atan", kind: "function", detail: "atan(x)" },
  { label: "atan2", kind: "function", detail: "atan2(y, x)" },
  { label: "floor", kind: "function", detail: "floor(x)" },
  { label: "ceil", kind: "function", detail: "ceil(x)" },
  { label: "round", kind: "function", detail: "round(x)" },
  { label: "ln", kind: "function", detail: "ln(x)" },
  { label: "log", kind: "function", detail: "log(x)" },
  { label: "pow", kind: "function", detail: "pow(base, exponent)" },
  { label: "sqrt", kind: "function", detail: "sqrt(x)" },
  { label: "exp", kind: "function", detail: "exp(x)" },
  { label: "rands", kind: "function", detail: "rands(min, max, count, seed)" },
  { label: "min", kind: "function", detail: "min(a, b, ...)" },
  { label: "max", kind: "function", detail: "max(a, b, ...)" },
  { label: "norm", kind: "function", detail: "norm(v)" },
  { label: "cross", kind: "function", detail: "cross(a, b)" },
  { label: "len", kind: "function", detail: "len(x)" },
  { label: "concat", kind: "function", detail: "concat(a, b, ...)" },
  { label: "lookup", kind: "function", detail: "lookup(key, table)" },
  { label: "str", kind: "function", detail: "str(values...)" },
  { label: "chr", kind: "function", detail: "chr(x)" },
  { label: "ord", kind: "function", detail: "ord(s)" },
  { label: "search", kind: "function", detail: "search(match, table)" },
  { label: "version", kind: "function", detail: "version()" },
  { label: "version_num", kind: "function", detail: "version_num()" },
  { label: "is_undef", kind: "function", detail: "is_undef(x)" },
  { label: "is_bool", kind: "function", detail: "is_bool(x)" },
  { label: "is_num", kind: "function", detail: "is_num(x)" },
  { label: "is_string", kind: "function", detail: "is_string(x)" },
  { label: "is_list", kind: "function", detail: "is_list(x)" },
  { label: "is_function", kind: "function", detail: "is_function(x)" },
];

/** OpenSCAD-defined special variables — the fixed set lint.ts checks `$name`
 * references against (a user can still assign their own `$name`, which the
 * linter tracks separately as it's encountered). */
export const SPECIAL_VARIABLES: BuiltinEntry[] = [
  { label: "$fn", kind: "special-variable", detail: "facet count override" },
  { label: "$fa", kind: "special-variable", detail: "minimum facet angle" },
  { label: "$fs", kind: "special-variable", detail: "minimum facet size" },
  { label: "$t", kind: "special-variable", detail: "animation step, 0..1" },
  { label: "$vpr", kind: "special-variable", detail: "viewport rotation" },
  { label: "$vpt", kind: "special-variable", detail: "viewport translation" },
  { label: "$vpd", kind: "special-variable", detail: "viewport camera distance" },
  { label: "$vpf", kind: "special-variable", detail: "viewport camera field of view" },
  { label: "$children", kind: "special-variable", detail: "number of module children" },
  { label: "$preview", kind: "special-variable", detail: "true during F5 preview" },
];

export const KEYWORDS: BuiltinEntry[] = [
  { label: "module", kind: "keyword", detail: "module name(params) { ... }", snippet: "module ${1:name}(${2:params}) {\n  ${3}\n}" },
  { label: "function", kind: "keyword", detail: "function name(params) = expr;", snippet: "function ${1:name}(${2:params}) = ${3:expr};" },
  { label: "if", kind: "keyword", detail: "if (cond) ...", snippet: "if (${1:condition}) {\n  ${2}\n}" },
  { label: "else", kind: "keyword", detail: "else ..." },
  { label: "for", kind: "keyword", detail: "for (i = [from:to]) ...", snippet: "for (${1:i} = [${2:0}:${3:1}:${4:10}]) {\n  ${5}\n}" },
  { label: "let", kind: "keyword", detail: "let (x = expr) ...", snippet: "let (${1:x} = ${2:expr}) {\n  ${3}\n}" },
  { label: "each", kind: "keyword", detail: "splice a list into a vector" },
  { label: "include", kind: "keyword", detail: "include <file.scad>", snippet: "include <${1:file.scad}>" },
  { label: "use", kind: "keyword", detail: "use <file.scad>", snippet: "use <${1:file.scad}>" },
];

export const CONSTANTS: BuiltinEntry[] = [
  { label: "true", kind: "constant", detail: "boolean" },
  { label: "false", kind: "constant", detail: "boolean" },
  { label: "undef", kind: "constant", detail: "the undefined value" },
];

export const ALL_BUILTINS: BuiltinEntry[] = [
  ...BUILTIN_MODULES,
  ...BUILTIN_FUNCTIONS,
  ...SPECIAL_VARIABLES,
  ...KEYWORDS,
  ...CONSTANTS,
];

/** Names known to the language itself — the linter's "known" set starts here
 * before adding whatever the file declares itself (see lint.ts). */
export const BUILTIN_CALL_NAMES = new Set([...BUILTIN_MODULES, ...BUILTIN_FUNCTIONS].map((e) => e.label));

export const KNOWN_SPECIAL_VARIABLES = new Set(SPECIAL_VARIABLES.map((e) => e.label.slice(1)));
