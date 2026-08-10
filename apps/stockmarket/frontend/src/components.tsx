import { useState } from "react";
import type { BriefView, SymbolView } from "./api";
import { seriesColor } from "./chart";
import { Button } from "@ap/ui/button";
import { Input } from "@ap/ui/field";

// Shared vocabulary for the stockmarket page. Colors are ALWAYS design tokens
// (var(--ds-chart-N)) — no raw values (the platform's no-raw-hex rule).

export const pct = (v: number | null) =>
  v === null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;

/** Direction class for a signed number — the only place up/down color lives. */
export const dir = (v: number | null) =>
  v === null || v === 0 ? "flat" : v > 0 ? "up" : "down";

/** Big number + session delta per symbol, the way every finance site opens.
 * This is what replaces an intraday 1D chart: the archive stores daily bars,
 * so the latest session is a number, not a line. */
export function StatTile({ view, colorIndex, onRemove }: {
  view: SymbolView; colorIndex: number; onRemove?: () => void;
}) {
  const loading = view.status === "pending";
  const broken = view.status === "invalid";
  return (
    <div className="sm-stat">
      <div className="sm-stat-head">
        <span className="sm-swatch" aria-hidden
              style={{ background: seriesColor(colorIndex) }} />
        <span className="sm-stat-sym">{view.symbol}</span>
        {onRemove && (
          <button type="button" className="sm-stat-x" onClick={onRemove}
                  aria-label={`Remove ${view.symbol} from watchlist`}>×</button>
        )}
      </div>
      {view.label && <div className="sm-stat-label">{view.label}</div>}
      {loading && <div className="sm-stat-state muted">backfilling…</div>}
      {broken && (
        <div className="sm-stat-state error" title={view.error}>
          unknown ticker
        </div>
      )}
      {!loading && !broken && (
        <>
          <div className="sm-stat-price">
            {view.latest_close === null ? "—" : view.latest_close.toFixed(2)}
          </div>
          <div className={`sm-stat-change ${dir(view.change_pct)}`}>
            {pct(view.change_pct)}
          </div>
          {view.latest_day && <div className="sm-stat-day muted">{view.latest_day}</div>}
        </>
      )}
    </div>
  );
}

export function TagChip({ tag }: { tag: string }) {
  return <span className="sm-tag">{tag}</span>;
}

/** The day's brief: prose, tags, and the movers that earned a mention. */
export function BriefCard({ brief }: { brief: BriefView }) {
  return (
    <article className="sm-brief">
      <header className="sm-brief-head">
        <h2>Market brief</h2>
        <span className="muted">{brief.day}</span>
      </header>
      {brief.indexes.length > 0 && (
        <div className="sm-brief-moves">
          {brief.indexes.map((i) => (
            <span key={i.symbol} className="sm-brief-move">
              <b>{i.symbol}</b>
              <span className={dir(i.return_pct)}>{pct(i.return_pct)}</span>
            </span>
          ))}
        </div>
      )}
      <p className="sm-brief-body">{brief.body}</p>
      {brief.movers.length > 0 && (
        <ul className="sm-movers">
          {brief.movers.map((m) => (
            <li key={`${m.index}-${m.symbol}`}>
              <b>{m.symbol}</b>
              {m.index && <span className="muted"> in {m.index}</span>}
              {m.contrib_bps !== null && (
                <span className={`sm-bps ${dir(m.contrib_bps)}`}>
                  {m.contrib_bps > 0 ? "+" : ""}{m.contrib_bps.toFixed(0)}bp
                </span>
              )}
              {m.note && <span className="sm-mover-note"> — {m.note}</span>}
            </li>
          ))}
        </ul>
      )}
      {brief.tags.length > 0 && (
        <div className="sm-tags">{brief.tags.map((t) => <TagChip key={t} tag={t} />)}</div>
      )}
    </article>
  );
}

export function AddSymbol({ onAdd, busy, full }: {
  onAdd: (symbol: string) => void; busy: boolean; full: boolean;
}) {
  const [value, setValue] = useState("");
  const ok = /^[A-Za-z0-9.^-]{1,12}$/.test(value.trim());
  return (
    <form className="sm-add" onSubmit={(e) => {
      e.preventDefault();
      if (ok && !busy && !full) { onAdd(value.trim().toUpperCase()); setValue(""); }
    }}>
      <Input placeholder="Add ticker (e.g. NVDA, SHOP.TO)" value={value}
             aria-label="Add a ticker to your watchlist" disabled={full}
             onChange={(e) => setValue(e.target.value)} />
      <Button type="submit" disabled={!ok || busy || full}>
        {busy ? "Adding…" : "Add"}
      </Button>
      {full && <span className="muted sm-note">Watchlist is full.</span>}
    </form>
  );
}
