export type SymbolView = {
  symbol: string;
  label: string;
  kind: "index" | "watch";
  status: "pending" | "ok" | "invalid";
  error: string;
  latest_day: string | null;
  latest_close: number | null;
  change_pct: number | null;      // the latest session's move
};

export type SeriesView = {
  symbol: string;
  points: [string, number][];     // (day, close), oldest first
  downsampled: boolean;
};

export type MoverView = {
  symbol: string;
  index: string;
  contrib_bps: number | null;
  note: string;
};

export type IndexNote = { symbol: string; return_pct: number; note: string };

export type BriefView = {
  day: string;
  body: string;
  tags: string[];
  indexes: IndexNote[];
  movers: MoverView[];
  run_id: string | null;
};

export type Summary = {
  indexes: SymbolView[];
  watchlist: SymbolView[];
  latest_day: string | null;
  latest_brief_day: string | null;
  tags: string[];
};

// The date ranges, matching what every finance site offers minus 1D: the
// archive holds daily bars only, so an intraday range would be one point
// pretending to be a line. The latest session's move is the big number
// instead.
export const RANGES = ["5D", "1M", "6M", "YTD", "1Y", "5Y"] as const;
export type Range = (typeof RANGES)[number];

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/apps/stockmarket/api${path}`, {
    credentials: "include",
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  });
  if (res.status === 401) {
    // The platform session guards this app — bounce to its login.
    window.location.href = "/login";
    throw new Error("401");
  }
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.status === 204 ? (undefined as T) : (res.json() as Promise<T>);
}
