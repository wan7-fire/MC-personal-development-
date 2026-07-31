import path from "node:path";
import type { AlignmentResult, InterfaceIndex, SnippetInfo } from "../types.js";
import { matchFunction } from "./matchFunction.js";

export function alignSnippet(index: InterfaceIndex, snippet: SnippetInfo): AlignmentResult {
  const result: AlignmentResult = { actions: [], matched: [], warnings: [] };

  for (const call of snippet.calls) {
    const fn = matchFunction(index, call.callee);
    if (!fn) {
      result.warnings.push(`No exported function matched: ${call.callee}`);
      continue;
    }

    result.matched.push(`${call.callee} -> ${fn.file}`);

    const hasImport = snippet.imports.some((imp) => imp.namedImports.includes(call.callee));
    if (!hasImport) {
      result.actions.push({
        type: "add-import",
        symbol: call.callee,
        from: toImportPath(fn.file)
      });
    }

    if (call.argCount > 1 && fn.params.length === 1) {
      const dtoType = fn.params[0]?.type;
      const typeInfo = dtoType ? index.types[dtoType]?.[0] : undefined;
      if (typeInfo && typeInfo.properties.length === call.argCount) {
        result.actions.push({
          type: "rewrite-call-to-object",
          callee: call.callee,
          objectType: dtoType!,
          fields: typeInfo.properties.map((p) => p.name)
        });
      }
    }
  }

  return result;
}

function toImportPath(file: string) {
  const normalized = file.replaceAll("\\\\", "/");
  const srcIndex = normalized.lastIndexOf("/src/");
  const withoutExt = normalized.replace(/\\.(tsx?|jsx?)$/, "");
  if (srcIndex >= 0) return "@/" + withoutExt.slice(srcIndex + 5);
  return "./" + path.basename(withoutExt);
}
