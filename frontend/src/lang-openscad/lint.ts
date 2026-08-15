import { linter, type Diagnostic } from "@codemirror/lint";
import { syntaxTree } from "@codemirror/language";
import type { EditorState } from "@codemirror/state";
import type { SyntaxNode } from "@lezer/common";
import { BUILTIN_CALL_NAMES, KNOWN_SPECIAL_VARIABLES } from "./builtins";

const MAX_SYNTAX_ERRORS = 50;

// A bare `=` inside an `if (...)` condition is never valid OpenSCAD (there's
// no assignment *expression*, only assignment *statements*), so it always
// produces a generic parse-error node too — this just recognizes the
// specific shape to give a better message than "Syntax error".
const ASSIGN_IN_CONDITION = /^[\w$]+\s*=\s*[^=]/;

// An AssignTarget's parent tells apart a real binding (`x = 1;`, a `for`/
// `let` clause) from a call's `name=value` argument override, which looks
// identical but doesn't declare anything.
function isBindingContext(parent: SyntaxNode | null): boolean {
  return parent?.type.name === "Assignment" || parent?.type.name === "SimpleAssignment";
}

/**
 * Real-time diagnostics from the Lezer parse tree: Lezer's own error-
 * recovery nodes (free syntax-error coverage) plus a few hand-written
 * semantic checks layered on top. See the project plan / README for the
 * scope and false-positive trade-offs of each check.
 *
 * Exported as a standalone function (rather than inlined in the `linter()`
 * call below) so it can be unit-tested directly against a plain
 * `EditorState`, without needing a real `EditorView`/DOM.
 */
export function computeDiagnostics(state: EditorState): Diagnostic[] {
  const tree = syntaxTree(state);
  const doc = state.doc;

  // --- Pass 1: collect declarations (forward reference is legal, so every
  // check below needs the whole file's declarations before it can judge any
  // single usage). ---
  const declaredCallable = new Set(BUILTIN_CALL_NAMES);
  const declaredSpecialVars = new Set(KNOWN_SPECIAL_VARIABLES);
  const firstDeclaration = new Map<string, SyntaxNode>();
  const duplicateDiagnostics: Diagnostic[] = [];
  let hasImports = false;

  tree.iterate({
    enter(ref) {
      if (ref.type.name === "IncludeDirective" || ref.type.name === "UseDirective") {
        hasImports = true;
      } else if (ref.type.name === "ModuleDeclaration" || ref.type.name === "FunctionDeclaration") {
        const node = ref.node;
        const idNode = node.getChild("Identifier");
        if (!idNode) return;
        const name = doc.sliceString(idNode.from, idNode.to);
        declaredCallable.add(name);
        const key = `${ref.type.name}:${name}`;
        const first = firstDeclaration.get(key);
        if (first) {
          const kindLabel = ref.type.name === "ModuleDeclaration" ? "module" : "function";
          duplicateDiagnostics.push({
            from: idNode.from,
            to: idNode.to,
            severity: "warning",
            message: `Duplicate ${kindLabel} '${name}' (also declared at line ${doc.lineAt(first.from).number})`,
          });
        } else {
          firstDeclaration.set(key, node);
        }
      } else if (ref.type.name === "AssignTarget" && isBindingContext(ref.node.parent)) {
        // Only real assignments (`$t = ...;`, a `for`/`let` binding) count
        // as declaring a special variable — a call's `$fn=...`-style named-
        // argument override (parent NamedArgument) does not, so a typo'd
        // override like `sphere(10, $fm=0.1)` still gets flagged below
        // instead of silently registering "$fm" as known from that point on.
        const specialNode = ref.node.getChild("SpecialVariable");
        if (specialNode) declaredSpecialVars.add(doc.sliceString(specialNode.from + 1, specialNode.to));
      }
    },
  });

  // --- Pass 2: syntax errors, unknown calls, $-typos, if(x=1) mistakes. ---
  const syntaxErrors: SyntaxNode[] = [];
  const assignInConditionRanges: { from: number; to: number }[] = [];
  const diagnostics: Diagnostic[] = [];

  tree.iterate({
    enter(ref) {
      if (ref.type.isError) {
        syntaxErrors.push(ref.node);
        return;
      }
      switch (ref.type.name) {
        case "CallExpression": {
          const idNode = ref.node.firstChild;
          if (idNode?.type.name !== "Identifier") return;
          const name = doc.sliceString(idNode.from, idNode.to);
          // Unresolvable include/use targets are the single biggest source
          // of false positives here (we don't parse what they export), so
          // stay conservative and skip the check entirely when either is
          // present rather than risk crying wolf on legitimate calls.
          if (!hasImports && !declaredCallable.has(name)) {
            diagnostics.push({
              from: idNode.from,
              to: idNode.to,
              severity: "warning",
              message: `Unknown module or function '${name}'`,
            });
          }
          return;
        }
        case "SpecialVariable": {
          const assignTarget = ref.node.parent;
          if (assignTarget?.type.name === "AssignTarget" && isBindingContext(assignTarget.parent)) {
            return; // a real binding, e.g. `$t = ...;` or `for ($t = ...)`
          }
          const name = doc.sliceString(ref.from + 1, ref.to);
          if (!declaredSpecialVars.has(name)) {
            diagnostics.push({
              from: ref.from,
              to: ref.to,
              severity: "warning",
              message: `Unknown special variable '$${name}' — check for a typo`,
            });
          }
          return;
        }
        case "IfStatement": {
          const condition = ref.node.getChild("Expression");
          if (!condition) return;
          const text = doc.sliceString(condition.from, condition.to);
          if (ASSIGN_IN_CONDITION.test(text)) {
            assignInConditionRanges.push({ from: condition.from, to: condition.to });
            diagnostics.push({
              from: condition.from,
              to: condition.to,
              severity: "error",
              message: "Did you mean '==' (comparison) instead of '=' (assignment)?",
            });
          }
          return;
        }
      }
    },
  });

  for (const node of syntaxErrors) {
    if (diagnostics.length >= MAX_SYNTAX_ERRORS) break;
    // Skip generic "Syntax error" noise inside a condition already covered
    // by the friendlier if(x=1) message above.
    if (assignInConditionRanges.some((r) => node.from >= r.from && node.to <= r.to)) continue;
    diagnostics.push({
      from: node.from,
      to: Math.max(node.to, node.from + 1),
      severity: "error",
      message: "Syntax error",
    });
  }

  return [...diagnostics, ...duplicateDiagnostics];
}

export const openscadLinter = linter((view) => computeDiagnostics(view.state), { delay: 150 });
