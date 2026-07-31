import path from "node:path";
import fg from "fast-glob";
import { Project } from "ts-morph";
import { addFunction, addType, createEmptyIndex } from "./interfaceIndex.js";
import { extractFunctions } from "./extractFunctions.js";
import { extractTypes } from "./extractTypes.js";

export async function scanProject(projectRoot: string) {
  const root = path.resolve(projectRoot);
  const files = await fg(["**/*.{ts,tsx}", "!**/node_modules/**", "!**/dist/**"], {
    cwd: root,
    absolute: true
  });

  const project = new Project({ skipAddingFilesFromTsConfig: true });
  project.addSourceFilesAtPaths(files);

  const index = createEmptyIndex();
  for (const sourceFile of project.getSourceFiles()) {
    for (const fn of extractFunctions(sourceFile)) addFunction(index, fn);
    for (const type of extractTypes(sourceFile)) addType(index, type);
  }

  return index;
}
