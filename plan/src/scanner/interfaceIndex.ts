import type { FunctionSymbol, InterfaceIndex, TypeSymbol } from "../types.js";

export function createEmptyIndex(): InterfaceIndex {
  return { functions: {}, types: {} };
}

export function addFunction(index: InterfaceIndex, symbol: FunctionSymbol) {
  index.functions[symbol.name] ??= [];
  index.functions[symbol.name].push(symbol);
}

export function addType(index: InterfaceIndex, symbol: TypeSymbol) {
  index.types[symbol.name] ??= [];
  index.types[symbol.name].push(symbol);
}
