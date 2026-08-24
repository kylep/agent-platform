import cronstrue from "cronstrue";

// Best-effort plain-English cron description for tooltips. Cron fires in the
// platform's clock (UTC) while timestamps beside it render local — label it
// so the two can't be misread as contradicting. Falls back to the raw
// expression on anything cronstrue can't parse.
export function cronEnglish(expr: string, zone?: string): string {
  try {
    return `${cronstrue.toString(expr)} (${zone || "UTC"})`;
  } catch {
    return expr;
  }
}

// IANA zones the browser knows, for timezone datalists. Guarded:
// Intl.supportedValuesOf is recent enough that an empty list is a fine
// degradation — the input still accepts a typed zone and the API validates it.
export function zoneOptions(): string[] {
  try {
    return Intl.supportedValuesOf("timeZone");
  } catch {
    return [];
  }
}
