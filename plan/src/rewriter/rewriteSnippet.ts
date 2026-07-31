import { Project, SyntaxKind } from "ts-morph";
import type { AlignmentAction } from "../types.js";

export function rewriteSnippet(filePath: string, actions: AlignmentAction[]) {
  const project = new Project({ skipAddingFilesFromTsConfig: true });
  const sourceFile = project.addSourceFileAtPath(filePath);

  for (const action of actions) {
    if (action.type === "add-import") {
      const exists = sourceFile.getImportDeclarations().some((imp) =>
        imp.getNamedImports().some((n) => n.getName() === action.symbol)
      );
      if (!exists) {
        sourceFile.addImportDeclaration({ namedImports: [action.symbol], moduleSpecifier: action.from });
      }
    }
  }

  sourceFile.forEachDescendant((node) => {
    if (!node.isKind(SyntaxKind.CallExpression)) return;
    const expr = node.getExpression();
    if (!expr.isKind(SyntaxKind.Identifier)) return;

    const action = actions.find((a) => a.type === "rewrite-call-to-object" && a.callee === expr.getText());
    if (!action || action.type !== "rewrite-call-to-object") return;

    const args = node.getArguments();
    if (args.length !== action.fields.length) return;

    const objectText = `{
${action.fields.map((field, index) => `  ${field}: ${args[index].getText()}`).join(",\n")}
}`;
    node.replaceWithText(`${expr.getText()}(${objectText})`);
  });

  return sourceFile.getFullText();
}
