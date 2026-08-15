import type { Completion, CompletionContext, CompletionResult } from "@codemirror/autocomplete";
import { snippetCompletion } from "@codemirror/autocomplete";
import { syntaxTree } from "@codemirror/language";
import type { SyntaxNode, Tree } from "@lezer/common";
import { Facet, Text } from "@codemirror/state";
import { ALL_BUILTINS, type BuiltinEntry } from "./builtins";
import { parser } from "./parser";

function completionType(kind: BuiltinEntry["kind"]): string {
  switch (kind) {
    case "module":
    case "function":
      return "function";
    case "special-variable":
      return "variable";
    case "keyword":
      return "keyword";
    case "constant":
      return "constant";
  }
}

function builtinToCompletion(entry: BuiltinEntry): Completion {
  const base = { type: completionType(entry.kind), detail: entry.detail };
  if (entry.snippet) return snippetCompletion(entry.snippet, { label: entry.label, ...base });
  return { label: entry.label, ...base };
}

/** Built once at module load — the static half of every completion popup. */
const STATIC_COMPLETIONS: Completion[] = ALL_BUILTINS.map(builtinToCompletion);

const NODE_TYPES_TO_SKIP = new Set(["Expression"]);

function paramNames(paramList: SyntaxNode | null, doc: Text): string {
  if (!paramList) return "";
  const names: string[] = [];
  for (let child = paramList.firstChild; child; child = child.nextSibling) {
    if (child.type.name !== "Param") continue;
    const idNode = child.getChild("Identifier");
    if (idNode) names.push(doc.sliceString(idNode.from, idNode.to));
  }
  return names.join(", ");
}

/**
 * Walks the parse tree for user-declared module/function names (anywhere in
 * the file — OpenSCAD allows forward reference, and a module can be
 * declared inside another module's body) and top-level variable
 * assignments (deliberately *not* module-local ones, which aren't visible
 * from wherever the user is currently typing). Expression subtrees are
 * pruned since none of these declarations can occur inside one.
 */
export function collectUserSymbols(tree: Tree, doc: Text): Completion[] {
  const out: Completion[] = [];
  const seen = new Set<string>();

  tree.iterate({
    enter(ref) {
      if (NODE_TYPES_TO_SKIP.has(ref.type.name)) return false;

      if (ref.type.name === "ModuleDeclaration" || ref.type.name === "FunctionDeclaration") {
        const node = ref.node;
        const idNode = node.getChild("Identifier");
        if (!idNode) return;
        const name = doc.sliceString(idNode.from, idNode.to);
        const key = `${ref.type.name}:${name}`;
        if (seen.has(key)) return;
        seen.add(key);
        out.push({
          label: name,
          type: "function",
          detail: `${name}(${paramNames(node.getChild("ParamList"), doc)})`,
          boost: 2,
        });
        return;
      }

      if (ref.type.name === "Assignment" && ref.node.parent?.type.name === "Program") {
        const idNode = ref.node.getChild("AssignTarget")?.getChild("Identifier");
        if (!idNode) return; // top-level $special = ...; already covered by builtins/lint, not worth a variable completion
        const name = doc.sliceString(idNode.from, idNode.to);
        const key = `Assignment:${name}`;
        if (seen.has(key)) return;
        seen.add(key);
        out.push({ label: name, type: "variable", detail: "variable", boost: 2 });
      }
    },
  });

  return out;
}

/**
 * A tool source's module declarations (not functions — a tool's toolbar
 * button represents the shapes it builds, and that's what its hover
 * tooltip in ScadToolbar previews), formatted as `name(params)` — the same
 * signature format collectUserSymbols shows in the completion popup, so a
 * module reads identically whether you're browsing the toolbar or typing.
 */
export function listToolModules(body: string): string[] {
  const tree = parser.parse(body);
  const doc = Text.of(body.split("\n"));
  const out: string[] = [];

  tree.iterate({
    enter(ref) {
      if (NODE_TYPES_TO_SKIP.has(ref.type.name)) return false;
      if (ref.type.name !== "ModuleDeclaration") return;
      const idNode = ref.node.getChild("Identifier");
      if (!idNode) return;
      const name = doc.sliceString(idNode.from, idNode.to);
      out.push(`${name}(${paramNames(ref.node.getChild("ParamList"), doc)})`);
    },
  });

  return out;
}

/**
 * Bodies of every tool available to autocomplete from, keyed by name — the
 * full library (see ToolsSettings), not just the ones the buffer currently
 * `use`s; which of these are actually in play is worked out per-completion
 * below from the buffer's own `use </include <tools/...>;` lines, so this
 * only needs updating when the tool library itself changes (see
 * ScadWorkspace.tsx), not on every keystroke.
 */
export const toolSourcesFacet = Facet.define<Record<string, string>, Record<string, string>>({
  combine: (values) => values[values.length - 1] ?? {},
});

const TOOL_PATH = /^tools\/([a-z0-9-]+)\.scad$/;

/** Names referenced by `use <tools/<name>.scad>;` or `include <...>;`
 * directives anywhere in the tree — mirrors extractReferencedToolNames in
 * ScadWorkspace.tsx (which drives the worker's virtual FS at render time)
 * but works off the parse tree already on hand instead of a second regex
 * pass over the raw text. */
function referencedToolNames(tree: Tree, doc: Text): Set<string> {
  const names = new Set<string>();
  tree.iterate({
    enter(ref) {
      if (ref.type.name !== "UseDirective" && ref.type.name !== "IncludeDirective") return;
      const pathNode = ref.node.getChild("IncludePath");
      if (!pathNode) return;
      // IncludePath spans the angle brackets themselves, e.g. `<tools/foo.scad>`.
      const inner = doc.sliceString(pathNode.from + 1, pathNode.to - 1).trim();
      const match = TOOL_PATH.exec(inner);
      if (match) names.add(match[1]);
    },
  });
  return names;
}

/** Parses a referenced tool's own source and collects its module/function
 * declarations the same way collectUserSymbols does for the main buffer, so
 * a `use <tools/foo.scad>;` line makes foo's modules autocomplete just like
 * ones declared locally — matching what actually resolves at render time
 * (see extractToolFiles in ScadWorkspace.tsx). Lower-boosted than the main
 * file's own symbols so local declarations still sort first. */
function collectToolSymbols(name: string, body: string): Completion[] {
  const tree = parser.parse(body);
  const doc = Text.of(body.split("\n"));
  return collectUserSymbols(tree, doc).map((c) => ({
    ...c,
    detail: `${c.detail} — tools/${name}.scad`,
    boost: 1,
  }));
}

const NON_CODE_NODES = new Set(["String", "LineComment", "BlockComment", "IncludePath"]);

function isInsideNonCode(tree: Tree, pos: number): boolean {
  for (let node: SyntaxNode | null = tree.resolveInner(pos, -1); node; node = node.parent) {
    if (NON_CODE_NODES.has(node.type.name)) return true;
  }
  return false;
}

export function openscadCompletionSource(context: CompletionContext): CompletionResult | null {
  const tree = syntaxTree(context.state);
  if (isInsideNonCode(tree, context.pos)) return null;

  const word = context.matchBefore(/[\w$]*/);
  if (!word || (word.from === word.to && !context.explicit)) return null;

  const options = [...STATIC_COMPLETIONS, ...collectUserSymbols(tree, context.state.doc)];

  const toolSources = context.state.facet(toolSourcesFacet);
  for (const name of referencedToolNames(tree, context.state.doc)) {
    const body = toolSources[name];
    if (body !== undefined) options.push(...collectToolSymbols(name, body));
  }

  return { from: word.from, options, validFor: /^[\w$]*$/ };
}
