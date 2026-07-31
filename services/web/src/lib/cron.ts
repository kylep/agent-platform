import cronstrue from "cronstrue";

// Best-effort plain-English cron description for tooltips. Cron fires in the
// platform's clock (UTC) while timestamps beside it render local — label it
// so the two can't be misread as contradicting. Falls back to the raw
// expression on anything cronstrue can't parse.
export function cronEnglish(expr: string): string {
  try {
    return `${cronstrue.toString(expr)} (UTC)`;
  } catch {
    return expr;
  }
}
