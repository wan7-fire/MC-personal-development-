import { SourceFile, SyntaxKind } from "ts-morph";
import type { PropertySymbol, TypeSymbol } from "../types.js";

export function extractTypes(sourceFile: SourceFile): TypeSymbol[] {
  const symbols: TypeSymbol[] = [];

  for (const iface of sourceFile.getInterfaces()) {
    if (!iface.isExported()) continue;
    symbols.push({
      name: iface.getName(),
      kind: "interface",
      properties: iface.getProperties().map((p) => ({
        name: p.getName(),
        type: p.getTypeNode()?.getText(),
        required: !p.hasQuestionToken()
      })),
      file: sourceFile.getFilePath(),
      exported: true
    });
  }

  for (const alias of sourceFile.getTypeAliases()) {
    if (!alias.isExported()) continue;
    const node = alias.getTypeNode();
    const properties: PropertySymbol[] = [];
    if (node?.getKind() === SyntaxKind.TypeLiteral) {
      for (const member of node.asKindOrThrow(SyntaxKind.TypeLiteral).getMembers()) {
        if (member.getKind() !== SyntaxKind.PropertySignature) continue;
        const prop = member.asKindOrThrow(SyntaxKind.PropertySignature);
        properties.push({
          name: prop.getName(),
          type: prop.getTypeNode()?.getText(),
          required: !prop.hasQuestionToken()
        });
      }
    }
    symbols.push({
      name: alias.getName(),
      kind: "type",
      properties,
      file: sourceFile.getFilePath(),
      exported: true
    });
  }

  return symbols;
}
