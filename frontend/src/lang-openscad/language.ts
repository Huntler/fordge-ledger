import { LRLanguage, LanguageSupport, delimitedIndent, foldNodeProp, foldInside, indentNodeProp } from "@codemirror/language";
import { parser } from "./parser";
import { openscadHighlight } from "./highlight";
import { openscadCompletionSource } from "./completions";
import { openscadLinter } from "./lint";

const parserWithMetadata = parser.configure({
  props: [
    openscadHighlight,
    indentNodeProp.add({
      Block: delimitedIndent({ closing: "}" }),
      ParamList: delimitedIndent({ closing: ")" }),
      ArgList: delimitedIndent({ closing: ")" }),
      VectorExpression: delimitedIndent({ closing: "]" }),
    }),
    foldNodeProp.add({
      Block: foldInside,
      ParamList: foldInside,
      ArgList: foldInside,
      VectorExpression: foldInside,
      BlockComment: (node) => ({ from: node.from + 2, to: node.to - 2 }),
    }),
  ],
});

export const openscadLanguage = LRLanguage.define({
  parser: parserWithMetadata,
  languageData: {
    commentTokens: { line: "//", block: { open: "/*", close: "*/" } },
    closeBrackets: { brackets: ["(", "[", "{", '"'] },
    autocomplete: openscadCompletionSource,
    indentOnInput: /^\s*[}\])]$/,
  },
});

/** The OpenSCAD `LanguageSupport` — highlighting, lint, and autocomplete all
 * come bundled, so wiring this into a CodeMirror `extensions` array is
 * enough on its own (generic UI like `lintGutter()`/`autocompletion()` still
 * needs to be added by the caller — see ScadWorkspace.tsx). */
export function openscad(): LanguageSupport {
  return new LanguageSupport(openscadLanguage, [openscadLinter]);
}
