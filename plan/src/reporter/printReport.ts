import type { AlignmentResult } from "../types.js";

export function printReport(result: AlignmentResult) {
  console.log("AI Aligner Report\n");

  if (result.matched.length) {
    console.log("Matched:");
    for (const item of result.matched) console.log(`- ${item}`);
    console.log("");
  }

  if (result.actions.length) {
    console.log("Actions:");
    for (const action of result.actions) console.log(`- ${JSON.stringify(action)}`);
    console.log("");
  }

  if (result.warnings.length) {
    console.log("Warnings:");
    for (const warning of result.warnings) console.log(`- ${warning}`);
    console.log("");
  }
}
