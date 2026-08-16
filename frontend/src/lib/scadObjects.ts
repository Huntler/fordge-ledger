/**
 * Lists the SCAD buffer's top-level "objects" — one entry per geometry-
 * producing statement (a module call chain, `if`/`for`/`let`, or a bare
 * `{ }` block) — for the vertical stack next to the STL preview (see
 * ScadObjectList.tsx), and builds the edit that toggles one on/off by
 * commenting its source lines out (or restoring them).
 *
 * Reuses the same Lezer parser CodeMirror's language support and linter
 * already parse the buffer with (see ../lang-openscad) rather than a
 * second hand-rolled scan — it already knows exactly where one top-level
 * statement ends and the next begins, including multi-line ones.
 */
import type { SyntaxNode } from "@lezer/common";
import { parser } from "../lang-openscad/parser";

export interface ScadObject {
  /** True while this object's statement is live in the buffer; false once
   * this panel has commented it out (see toggleScadObject). */
  active: boolean;
  /** Human-readable label, e.g. "cube", "translate › sphere", "difference". */
  label: string;
  /** The statement's own source, regardless of active state — the live text
   * when active, or the text unwrapped from its hidden comment when not.
   * Used as the thumbnail cache key (see ScadWorkspace) and to reconstruct
   * an isolated single-object source (see isolateScadObject). */
  text: string;
  /** Span to replace when toggling — the bare statement itself when active,
   * or its `/*@hidden ... @hidden*\/` wrapper when inactive. */
  from: number;
  to: number;
}

// Statement kinds that aren't a placeable "object" of their own —
// declarations, imports, plain variable assignments, and bare `;`s carry no
// geometry, so they don't get a row in the stack.
const NON_OBJECT_STATEMENTS = new Set([
  "ModuleDeclaration",
  "FunctionDeclaration",
  "IncludeDirective",
  "UseDirective",
  "Assignment",
  "EmptyStatement",
  "LineComment",
]);

// Wrapper this panel writes around a statement it comments out, so
// computeScadObjects can tell it apart from a block comment the user wrote
// by hand (left alone) and restore the original text verbatim. `[\s\S]`
// rather than `.` crosses the newlines a multi-line statement still has
// once wrapped.
const HIDDEN_RE = /^\/\*@hidden\n([\s\S]*)\n@hidden\*\/$/;

function summarize(text: string): string {
  const firstLine = text.trim().split("\n", 1)[0].replace(/\s+/g, " ").trim();
  return firstLine.length > 44 ? `${firstLine.slice(0, 43)}…` : firstLine;
}

// Follows a chain of bare `transform() transform() shape();` calls down to
// its leaf, e.g. "translate › rotate › cube" — stops at the first child
// that isn't itself a plain call (a `{ }` block, control statement, or a
// bare `;`), so `difference() { cube(); cylinder(); }` is labeled just
// "difference" rather than reaching into its operands.
function callChainLabel(node: SyntaxNode, code: string): string {
  const names: string[] = [];
  let current: SyntaxNode | null = node;
  while (current && current.type.name === "ModuleCallStatement") {
    const idNode = current.getChild("CallExpression")?.firstChild;
    names.push(idNode ? code.slice(idNode.from, idNode.to) : "?");
    const next: SyntaxNode | null = current.lastChild;
    current = next?.type.name === "ModuleCallStatement" ? next : null;
  }
  return names.join(" › ");
}

function labelFor(node: SyntaxNode, code: string): string {
  if (node.type.name === "ModuleCallStatement") return callChainLabel(node, code);
  return summarize(code.slice(node.from, node.to));
}

/** Parses `code` and lists every top-level object in source order — live
 * statements plus any block this panel previously commented out, so a
 * toggled-off entry keeps its place in the stack instead of disappearing. */
export function computeScadObjects(code: string): ScadObject[] {
  const tree = parser.parse(code);
  const objects: ScadObject[] = [];
  for (let node = tree.topNode.firstChild; node; node = node.nextSibling) {
    if (node.type.name === "BlockComment") {
      const match = HIDDEN_RE.exec(code.slice(node.from, node.to));
      if (match) {
        objects.push({ active: false, label: summarize(match[1]), text: match[1], from: node.from, to: node.to });
      }
      continue;
    }
    if (NON_OBJECT_STATEMENTS.has(node.type.name)) continue;
    const text = code.slice(node.from, node.to);
    objects.push({ active: true, label: labelFor(node, code), text, from: node.from, to: node.to });
  }
  return objects;
}

/**
 * Builds the `{from, to, insert}` edit that flips one object's active
 * state. Returns null if an active statement's own text contains `*\/`
 * (e.g. inside a string literal) — wrapping it would terminate the comment
 * early and corrupt the file, so the caller should refuse the toggle
 * instead of applying it.
 */
export function toggleScadObject(
  code: string,
  object: ScadObject,
): { from: number; to: number; insert: string } | null {
  const text = code.slice(object.from, object.to);
  if (object.active) {
    if (text.includes("*/")) return null;
    return { from: object.from, to: object.to, insert: `/*@hidden\n${text}\n@hidden*/` };
  }
  const match = HIDDEN_RE.exec(text);
  return { from: object.from, to: object.to, insert: match ? match[1] : text };
}

/**
 * Builds a variant of `code` with every top-level object *except* `target`
 * commented out — declarations, imports and assignments (a target's
 * geometry may depend on those) are left untouched, only the sibling
 * objects are silenced. Used to render a single object's low-quality
 * thumbnail (see ScadWorkspace) without a second, separate parse of just
 * that statement, which would drop any variables/modules it relies on.
 *
 * `target` itself is left (or restored, if it was already commented out)
 * active, regardless of its own `active` flag — a thumbnail should show
 * what a hidden object *would* look like, not a blank render.
 *
 * Edits apply back-to-front (descending `from`) so earlier objects' spans,
 * computed once against the original `code`, stay valid as later ones in
 * the loop rewrite the string.
 */
export function isolateScadObject(code: string, target: ScadObject, allObjects: ScadObject[]): string {
  let result = code;
  const ordered = [...allObjects].sort((a, b) => b.from - a.from);
  for (const object of ordered) {
    if (object === target) {
      if (!object.active) result = result.slice(0, object.from) + object.text + result.slice(object.to);
      continue;
    }
    if (!object.active) continue; // already commented out — nothing to do
    if (object.text.includes("*/")) continue; // can't safely wrap — left visible, a rare over-inclusive thumbnail
    result =
      result.slice(0, object.from) +
      `/*@hidden\n${object.text}\n@hidden*/` +
      result.slice(object.to);
  }
  return result;
}
