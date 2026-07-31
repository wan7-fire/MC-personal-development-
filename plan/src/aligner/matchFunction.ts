import type { FunctionSymbol, InterfaceIndex } from "../types.js";

export function matchFunction(index: InterfaceIndex, name: string): FunctionSymbol | undefined {
  const candidates = index.functions[name] ?? [];
  if (candidates.length === 1) return candidates[0];
  return candidates.find((c) => c.exported);
}
