import { styleTags, tags as t } from "@lezer/highlight";

/**
 * Maps grammar node names (see openscad.grammar / parser.terms.js) to
 * highlight tags. Path selectors ("Parent/Child") pick out a node only when
 * it appears directly under a specific parent, so e.g. an Identifier naming
 * a module declaration highlights differently from one referencing a call.
 */
export const openscadHighlight = styleTags({
  module: t.definitionKeyword,
  function: t.definitionKeyword,
  "if else": t.controlKeyword,
  for: t.controlKeyword,
  let: t.controlKeyword,
  each: t.controlKeyword,
  "include use": t.moduleKeyword,
  true: t.bool,
  false: t.bool,
  undef: t.null,

  "ModuleDeclaration/Identifier FunctionDeclaration/Identifier": t.definition(t.function(t.variableName)),
  "CallExpression/Identifier": t.function(t.variableName),
  "Param/Identifier": t.definition(t.variableName),
  "AssignTarget/Identifier": t.definition(t.variableName),
  "AssignTarget/SpecialVariable": t.definition(t.special(t.variableName)),
  Identifier: t.variableName,
  SpecialVariable: t.special(t.variableName),

  Number: t.number,
  String: t.string,
  IncludePath: t.string,
  LineComment: t.lineComment,
  BlockComment: t.blockComment,

  Modifier: t.modifier,

  "( )": t.paren,
  "[ ]": t.squareBracket,
  "{ }": t.brace,
  ",": t.separator,
  ";": t.punctuation,
  "? :": t.controlOperator,

  // @lezer/highlight's selector mini-language uses bare "/" and "!" as path
  // syntax, so operator tokens containing them (division "/", "!", "!=")
  // must be individually JSON-quoted to be read as literal node names.
  "=": t.definitionOperator,
  '"==" "!="': t.compareOperator,
  "< <= > >=": t.compareOperator,
  "&& ||": t.logicOperator,
  '"!"': t.logicOperator,
  '"+" "-" "*" "/" "%"': t.arithmeticOperator,
});
