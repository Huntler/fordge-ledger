import type { Completion, CompletionContext, CompletionResult } from "@codemirror/autocomplete";
import { snippetCompletion } from "@codemirror/autocomplete";
import { syntaxTree } from "@codemirror/language";
import type { SyntaxNode, Tree } from "@lezer/common";
import type { Text } from "@codemirror/state";
import { ALL_BUILTINS, type BuiltinEntry } from "./builtins";

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

  return {
    from: word.from,
    options: [...STATIC_COMPLETIONS, ...collectUserSymbols(tree, context.state.doc)],
    validFor: /^[\w$]*$/,
  };
}
