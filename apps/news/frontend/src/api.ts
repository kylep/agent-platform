export type TopicView = {
  slug: string;
  label: string;
  color: number;        // --ds-chart-N index
  count: number;
  spark: number[];      // 14 days, oldest first
};

export type ItemView = {
  id: string;
  title: string;
  url: string;
  source: string;
  summary: string;
  topic: string;
  topic_label: string;
  color: number;
  day: string;
  run_id: string | null;
};

export type CalendarDay = { total: number; by_topic: Record<string, number> };
export type Summary = {
  today: number; week: number; total: number; topics: number;
  latest_day: string | null;
};

export async function api<T>(path: string): Promise<T> {
  const res = await fetch(`/apps/news/api${path}`, { credentials: "include" });
  if (res.status === 401) {
    // The platform session guards this app — bounce to its login.
    window.location.href = "/login";
    throw new Error("401");
  }
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json() as Promise<T>;
}
