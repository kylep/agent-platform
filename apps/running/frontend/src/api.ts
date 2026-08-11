// Typed client for the running app's browse API. Same shape as the
// stockmarket app's api.ts: credentials included (the session cookie the nginx
// auth_request checks), JSON in and out.

export type Totals = {
  total_km: number;
  total_elevation_m: number;
  total_moving_time_s: number;
  activities: number;
  runs: number;
  everests: number;
  comparison: string | null;
};

export type Summary = {
  totals: Totals;
  latest_day: string | null;
  latest_brief_week: string | null;
  sync_after: string;
  today: string;
  tags: string[];
};

export type HeatDay = { day: string; distance_km: number; count: number };
export type Calendar = { weeks: number; days: HeatDay[] };

export type WeekBar = {
  week_start: string;
  distance_km: number;
  runs: number;
  moving_time_s: number;
};

export type Pr = { km?: number; day?: string; pace?: string; week_start?: string };
export type Prs = {
  longest_run: Pr | null;
  fastest_5k: Pr | null;
  fastest_10k: Pr | null;
  biggest_week: Pr | null;
  longest_streak: number;
  current_streak: number;
};

export type Brief = {
  week_start: string;
  body: string;
  highlights: string[];
  tags: string[];
  distance_km: number;
  runs: number;
  run_id: string | null;
};

export type Activity = {
  id: number;
  day: string;
  name: string;
  type: string;
  distance_km: number;
  moving_time_s: number;
  pace: string | null;
  elevation_m: number | null;
  avg_hr: number | null;
};

const BASE = "/apps/running/api";

export async function api<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { credentials: "include" });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json() as Promise<T>;
}
