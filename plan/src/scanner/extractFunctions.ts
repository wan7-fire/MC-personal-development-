import { SourceFile, SyntaxKind } from "ts-morph";
import type { FunctionSymbol } from "../types.js";

export function extractFunctions(sourceFile: SourceFile): FunctionSymbol[] {
  const symbols: FunctionSymbol[] = [];

  for (const fn of sourceFile.getFunctions()) {
    if (!fn.isExported()) continue;

    symbols.push({
      name: fn.getName() ?? "<anonymous>",
      params: fn.getParameters().map((p) => ({
        name: p.getName(),
        type: p.getTypeNode()?.getText()
      })),
      returnType: fn.getReturnTypeNode()?.getText(),
      file: sourceFile.getFilePath(),
      exported: true
    });
  }

  for (const statement of sourceFile.getVariableStatements()) {
    if (!statement.isExported()) continue;
    for (const decl of statement.getDeclarations()) {
      const init = decl.getInitializer();
      if (!init) continue;
      if (![SyntaxKind.ArrowFunction, SyntaxKind.FunctionExpression].includes(init.getKind())) continue;
      const fn = init.asKindOrThrow(init.getKind() as SyntaxKind.ArrowFunction | SyntaxKind.FunctionExpression);
      symbols.push({
        name: decl.getName(),
        params: fn.getParameters().map((p) => ({ name: p.getName(), type: p.getTypeNode()?.getText() })),
        returnType: fn.getReturnTypeNode()?.getText(),
        file: sourceFile.getFilePath(),
        exported: true
      });
    }
  }

  return symbols;
}
