import type { CSSProperties } from "react";
import "./quota.css";

// The two usage bars under the sidebar brand (docs/design/22). The shell
// stays data-free: the console fetches the snapshot and hands it here, and
// app frontends that never fetch one simply render nothing.

export type QuotaWindow = { utilization: number | null; resets_at: string | null };

/** `quota_store.serialize` — the one shape the REST body, the SSE frame and
 * the Kafka payload all carry. Every field is optional here because the
 * component is spread from a hook that may hold nothing yet. */
export type QuotaReading = {
  five_hour?: QuotaWindow | null;
  seven_day?: QuotaWindow | null;
  status?: string | null;
  observed_at?: string | null;
  source?: string | null;
  stale?: boolean;
  age_seconds?: number | null;
  probe?: string | null;
  provider?: "claude" | "codex";
};

export type QuotaSnapshot = QuotaReading & { codex?: QuotaReading | null };

// The backend rounds the same way (half up on a 0..1 fraction), so the number
// in the bar and the number in a Relay post about it are never one apart.
function percent(utilization: number | null | undefined): number | null {
  return typeof utilization === "number"
    ? Math.min(100, Math.max(0, Math.floor(utilization * 100 + 0.5))) : null;
}

/** "3d 3h", "3h 54m", "12m", "40s" — the backend's humanize_delta, so the label and the tool agree. */
function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return m ? `${m}m` : `${s}s`;
}

/** Seconds from `iso` to `now` — negative while `iso` is still ahead. */
function elapsed(iso: string | null | undefined, now: number): number | null {
  if (!iso) return null;
  const at = Date.parse(iso);
  return Number.isNaN(at) ? null : (now - at) / 1000;
}

function describe(name: string, pct: number | null, resets_at: string | null | undefined,
                  observed_at: string | null | undefined, now: number): string {
  if (pct === null) return `${name}: unknown`;
  // A reset already in the past is not a countdown — it is the reason the
  // snapshot is stale, and `stale` already says that.
  const toReset = elapsed(resets_at, now);
  const age = elapsed(observed_at, now);
  const head = toReset !== null && toReset < 0
    ? `${name}: ${pct}% used, resets in ${duration(-toReset)}`
    : `${name}: ${pct}% used`;
  return age !== null && age >= 0 ? `${head} · observed ${duration(age)} ago` : head;
}

function Bar({ name, fill, window: w, observed_at, stale, now }: {
  name: string; fill: string; window: QuotaWindow;
  observed_at?: string | null; stale?: boolean; now: number;
}) {
  const pct = percent(w.utilization);
  const label = describe(name, pct, w.resets_at, observed_at, now);
  const text = pct === null ? "" : `${pct}%`;
  return (
    <div className="quota-bar" role="meter" aria-label={label} title={label}
         // role="meter" REQUIRES a valuenow; an unknown window says 0 and
         // corrects itself in the valuetext, rather than shipping a meter
         // that fails validation.
         aria-valuenow={pct ?? 0} aria-valuemin={0} aria-valuemax={100}
         aria-valuetext={pct === null ? "unknown" : `${pct}%`}
         data-stale={stale && pct !== null ? "" : undefined}
         style={{ "--quota-fill": fill, "--quota-pct": `${pct ?? 0}%` } as CSSProperties}>
      {/* Both copies are decoration: the meter's own label is what a screen
          reader reads, and it says more than "22%" does. */}
      <span className="quota-label" aria-hidden="true">{text}</span>
      <span className="quota-clip" aria-hidden="true">
        <span className="quota-label quota-label-on">{text}</span>
      </span>
    </div>
  );
}

function known(window: QuotaWindow | null | undefined): window is QuotaWindow {
  return typeof window?.utilization === "number";
}

export function QuotaBars({ five_hour, seven_day, observed_at, stale, codex }: QuotaSnapshot) {
  // Nothing known at all — before the first fetch, or after one that failed.
  // Placeholder bars would be a claim about usage; absence is not.
  if (!five_hour && !seven_day) return null;
  const now = Date.now();
  return (
    <div className="quota">
      <Bar name="Claude 5-hour window" fill="var(--ds-quota-5h)" now={now}
           window={five_hour ?? { utilization: null, resets_at: null }}
           observed_at={observed_at} stale={stale} />
      <Bar name="Claude 7-day window" fill="var(--ds-quota-7d)" now={now}
           window={seven_day ?? { utilization: null, resets_at: null }}
           observed_at={observed_at} stale={stale} />
      {codex && (known(codex.five_hour) || known(codex.seven_day)) && (
        <div className="quota-provider">
          {known(codex.five_hour) && (
            <Bar name="Codex 5-hour window" fill="var(--ds-quota-codex-5h)" now={now}
                 window={codex.five_hour} observed_at={codex.observed_at}
                 stale={codex.stale} />
          )}
          {known(codex.seven_day) && (
            <Bar name="Codex 7-day window" fill="var(--ds-quota-codex-7d)" now={now}
                 window={codex.seven_day} observed_at={codex.observed_at}
                 stale={codex.stale} />
          )}
        </div>
      )}
    </div>
  );
}
