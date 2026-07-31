#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { Command } from "commander";
import { scanProject } from "./scanner/scanProject.js";
import { parseSnippet } from "./parser/parseSnippet.js";
import { alignSnippet } from "./aligner/alignSnippet.js";
import { rewriteSnippet } from "./rewriter/rewriteSnippet.js";
import { createCodeDiff } from "./reporter/createDiff.js";
import { printReport } from "./reporter/printReport.js";

const program = new Command();

program
  .name("ai-aligner")
  .description("Align AI-generated code snippets with project interfaces")
  .version("0.1.0");

program
  .command("align")
  .requiredOption("--project <path>", "project root")
  .requiredOption("--snippet <path>", "snippet file")
  .option("--write", "write changes back to snippet file", false)
  .action(async (options) => {
    const projectRoot = path.resolve(options.project);
    const snippetPath = path.resolve(options.snippet);
    const before = fs.readFileSync(snippetPath, "utf8");

    const index = await scanProject(projectRoot);
    const snippet = parseSnippet(snippetPath);
    const result = alignSnippet(index, snippet);
    const after = rewriteSnippet(snippetPath, result.actions);

    printReport(result);
    console.log(createCodeDiff(path.basename(snippetPath), before, after));

    if (options.write) {
      fs.writeFileSync(snippetPath, after, "utf8");
      console.log(`Wrote: ${snippetPath}`);
    }
  });

program.parse();
