import cronstrue from "cronstrue";

// Best-effort plain-English cron description for tooltips; falls back to the
// raw expression on anything cronstrue can't parse.
export function cronEnglish(expr: string): string {
  try {
    return cronstrue.toString(expr);
  } catch {
    return expr;
  }
}
