import { Link } from "react-router-dom";
import type { CalendarDay, ItemView, TopicView } from "./api";

// Shared vocabulary for the news browser. Colors are ALWAYS design tokens
// (var(--ds-chart-N)) — no raw values (the platform's no-raw-hex rule).

export function TopicChip({ slug, label, color, count, active }: {
  slug: string; label: string; color: number; count?: number; active?: boolean;
}) {
  return (
    <Link to={`/topic/${slug}`} aria-current={active ? "page" : undefined}
          className={`topic-chip${active ? " active" : ""}`}
          style={{ ["--topic-color" as string]: `var(--ds-chart-${color})` }}>
      <span className="topic-dot" aria-hidden />
      {label}
      {count !== undefined && <span className="topic-count">{count}</span>}
    </Link>
  );
}

export function Sparkline({ values, color }: { values: number[]; color: number }) {
  const w = 120, h = 28, max = Math.max(...values, 1);
  const pts = values.map((v, i) =>
    `${(i * (w - 4)) / Math.max(values.length - 1, 1) + 2},${h - 3 - (v / max) * (h - 6)}`);
  return (
    <svg className="topic-spark" viewBox={`0 0 ${w} ${h}`} width={w} height={h} aria-hidden>
      <polyline points={pts.join(" ")} fill="none"
                stroke={`var(--ds-chart-${color})`} strokeWidth="1.5" />
    </svg>
  );
}

export function ItemRow({ item, showDay }: { item: ItemView; showDay?: boolean }) {
  return (
    <div className="news-item">
      <div className="news-item-head">
        <span className="topic-dot" aria-hidden
              style={{ ["--topic-color" as string]: `var(--ds-chart-${item.color})` }} />
        {item.url ? (
          <a href={item.url} target="_blank" rel="noopener noreferrer"
             className="news-item-title">{item.title}</a>
        ) : (
          <span className="news-item-title">{item.title}</span>
        )}
        {item.source && <span className="news-item-src">{item.source}</span>}
        {showDay && <Link className="news-item-day" to={`/day/${item.day}`}>{item.day}</Link>}
      </div>
      {item.summary && <p className="news-item-sum">{item.summary}</p>}
    </div>
  );
}

export function ItemList({ items, showDay }: { items: ItemView[]; showDay?: boolean }) {
  if (!items.length) return <p className="muted">Nothing here.</p>;
  return <div>{items.map((i) => <ItemRow key={i.id} item={i} showDay={showDay} />)}</div>;
}

// --- calendar ----------------------------------------------------------------

export function ym(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}
export function shiftMonth(month: string, by: number): string {
  const [y, m] = month.split("-").map(Number);
  return ym(new Date(y, m - 1 + by, 1));
}
export function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleString(undefined, { month: "long", year: "numeric" });
}
export function monthDays(month: string): (string | null)[] {
  const [y, m] = month.split("-").map(Number);
  const pad = (new Date(y, m - 1, 1).getDay() + 6) % 7;
  const count = new Date(y, m, 0).getDate();
  return [...Array.from({ length: pad }, () => null),
          ...Array.from({ length: count }, (_, i) =>
            `${month}-${String(i + 1).padStart(2, "0")}`)];
}
const WEEKDAYS = ["M", "T", "W", "T", "F", "S", "S"];

export function VolumeCalendar({ month, days, onOpen }: {
  month: string; days: Record<string, CalendarDay>;
  onOpen: (day: string) => void;
}) {
  const max = Math.max(...Object.values(days).map((d) => d.total), 1);
  return (
    <div className="vol-cal" role="group" aria-label={`News volume, ${monthLabel(month)}`}>
      {WEEKDAYS.map((d, i) => <div key={`${d}${i}`} className="vol-head">{d}</div>)}
      {monthDays(month).map((day, i) => {
        if (day === null) return <div key={`p${i}`} className="vol-cell vol-pad" />;
        const n = days[day]?.total ?? 0;
        const heat = n ? 0.25 + 0.75 * (n / max) : 0;
        return (
          <button key={day} type="button" disabled={!n} onClick={() => onOpen(day)}
                  className="vol-cell"
                  style={n ? { ["--heat" as string]: String(heat) } : undefined}
                  aria-label={`${day}: ${n} items`}>
            {Number(day.slice(-2))}
          </button>
        );
      })}
    </div>
  );
}

export function topicIndex(topics: TopicView[]): Map<string, TopicView> {
  return new Map(topics.map((t) => [t.slug, t]));
}
