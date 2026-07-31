import { Project, SyntaxKind } from "ts-morph";
import type { CallInfo, ImportInfo, SnippetInfo } from "../types.js";

export function parseSnippet(filePath: string): SnippetInfo {
  const project = new Project({ skipAddingFilesFromTsConfig: true });
  const sourceFile = project.addSourceFileAtPath(filePath);

  const calls: CallInfo[] = [];
  const imports: ImportInfo[] = [];

  for (const imp of sourceFile.getImportDeclarations()) {
    imports.push({
      namedImports: imp.getNamedImports().map((n) => n.getName()),
      moduleSpecifier: imp.getModuleSpecifierValue()
    });
  }

  sourceFile.forEachDescendant((node) => {
    if (!node.isKind(SyntaxKind.CallExpression)) return;
    const expr = node.getExpression();
    if (!expr.isKind(SyntaxKind.Identifier)) return;
    calls.push({ callee: expr.getText(), argCount: node.getArguments().length });
  });

  return { calls, imports };
}
