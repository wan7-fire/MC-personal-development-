import { createPatch } from "diff";

export function createCodeDiff(fileName: string, before: string, after: string) {
  return createPatch(fileName, before, after, "before", "after");
}
