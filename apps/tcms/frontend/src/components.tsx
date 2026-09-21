import { useState, type CSSProperties } from "react";
import { Chip } from "@ap/ui/chip";
import type { AgentFaces, PyramidLayer, ResultStatus, Totals } from "./api";

// Shared display pieces for the tcms pages. Charts are hand-drawn SVG and
// CSS (no charting library); colours are design tokens only, so the pyramid
// and the sparkline read the same in both themes.

// --- formatting ---------------------------------------------------------------

/** Seconds as the label the design writes: `3 m 40 s`, `12 s`, `1 h 02 m`. */
export function fmtSeconds(s: number): string {
  const total = Math.round(s);
  if (total < 60) return `${total} s`;
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  if (h) return `${h} h ${String(m).padStart(2, "0")} m`;
  return sec ? `${m} m ${sec} s` : `${m} m`;
}

export function fmtMs(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
  return fmtSeconds(ms / 1000);
}

export function shortSha(sha: string | null | undefined): string {
  return (sha ?? "").slice(0, 7) || "—";
}

/** An ISO timestamp as a compact local date-time; the raw value rides on
 * `title` wherever this is rendered. */
export function fmtWhen(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export function pct(v: number | null | undefined, digits = 1): string {
  return v == null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

// --- status vocabulary ----------------------------------------------------------

const STATUS_VARIANT: Record<ResultStatus, "ok" | "danger" | "warn" | "neutral"> = {
  pass: "ok", fail: "danger", error: "danger", flaky: "warn", skip: "neutral",
};

export function ResultChip({ status }: { status: ResultStatus }) {
  return <Chip variant={STATUS_VARIANT[status] ?? "neutral"}>{status}</Chip>;
}

/** The totals as chips, zeros left out: a green run is one chip. */
export function TotalsChips({ totals }: { totals: Totals }) {
  const order: ResultStatus[] = ["pass", "fail", "error", "flaky", "skip"];
  const shown = order.filter((k) => (totals[k] ?? 0) > 0);
  if (!shown.length) return <span className="muted">no results</span>;
  return (
    <span className="tc-chips">
      {shown.map((k) => (
        <Chip key={k} variant={STATUS_VARIANT[k]}>{totals[k]} {k}</Chip>
      ))}
    </span>
  );
}

/** One result as a dot; a row of them is a ref's recent history. */
export function StatusDot({ status, title }: { status: ResultStatus | null | undefined; title?: string }) {
  return <span className={`tc-dot tc-dot-${status ?? "none"}`} title={title} role="img"
               aria-label={status ?? "no result"} />;
}

export function VerifyMark({ ok }: { ok: boolean | null }) {
  if (ok == null) return <span className="muted" title="no verify.json recorded">—</span>;
  return ok
    ? <span className="tc-verify-ok" title="ap-verify passed">✓</span>
    : <span className="tc-verify-bad" title="ap-verify failed">✗</span>;
}

// --- faces -------------------------------------------------------------------------

// Agents are people here too: the same emoji-on-a-hue-disc the console draws,
// read from /api/agents so it is the SAME face. An agent the platform does not
// list (or a run with none) gets a plain disc with its initial — a face is
// never missing, but the app never invents one.
export function AgentFace({ name, faces, size = 22 }: { name: string | null; faces: AgentFaces; size?: number }) {
  const face = name ? faces[name] : undefined;
  const [failed, setFailed] = useState<string | null>(null);
  const url = face?.image_url && face.image_url.startsWith("/api/artifacts/") ? face.image_url : null;
  const image = url && url !== failed ? url : null;
  const style = {
    "--face-hue": face?.hue ?? 0, width: size, height: size, fontSize: Math.round(size * 0.52),
  } as CSSProperties;
  return (
    <span className="tc-agent">
      <span className={`tc-face${face ? "" : " tc-face-plain"}`} style={style} aria-hidden="true">
        {image
          ? <img src={image} alt="" loading="lazy" onError={() => setFailed(image)} />
          : face?.emoji ?? (name ? name[0].toUpperCase() : "?")}
      </span>
      <span>{name ?? "—"}</span>
    </span>
  );
}

// --- charts ---------------------------------------------------------------------------

const PYRAMID_LABEL: Record<PyramidLayer["layer"], string> = {
  e2e: "e2e", integration: "integration", unit: "unit",
};

/** The test pyramid: three centred horizontal bars, e2e on top, unit at the
 * bottom, each as wide as its share of the largest layer. It reads as a
 * pyramid exactly when the suite is shaped like one — a top-heavy suite draws
 * top-heavy, which is the point of drawing it. */
export function Pyramid({ layers }: { layers: PyramidLayer[] }) {
  const max = Math.max(1, ...layers.map((l) => l.count));
  const empty = layers.every((l) => l.count === 0);
  return (
    <div className="tc-pyramid" role="img"
         aria-label={layers.map((l) => `${l.layer}: ${l.count} tests, ${fmtSeconds(l.seconds)}`).join("; ")}>
      {layers.map((l, i) => {
        // A floor keeps an empty or tiny layer visible as a sliver so the
        // shape stays legible and the label has a bar to sit on.
        const width = empty ? 0 : Math.max(4, (100 * l.count) / max);
        return (
          <div key={l.layer} className="tc-pyramid-row">
            <div className={`tc-pyramid-bar tc-pyramid-${i + 1}`} style={{ width: `${width}%` }} />
            <div className="tc-pyramid-label">
              <b>{PYRAMID_LABEL[l.layer]}</b> · {l.count} · {fmtSeconds(l.seconds)}
            </div>
          </div>
        );
      })}
      {empty && <p className="muted tc-pyramid-empty">No runs recorded yet.</p>}
    </div>
  );
}

/** A polyline over a series; `titles` become per-point tooltips. The y range
 * is [min, max] of the data (with a floor for a flat line) so a pass rate
 * that lives between 97% and 100% still shows its shape. */
export function Sparkline({ values, titles, width = 240, height = 48, min, max, className }: {
  values: (number | null)[];
  titles?: string[];
  width?: number;
  height?: number;
  min?: number;
  max?: number;
  className?: string;
}) {
  const nums = values.filter((v): v is number => v != null);
  if (nums.length === 0) return <span className="muted">no data</span>;
  const lo = min ?? Math.min(...nums);
  const hi = max ?? Math.max(...nums);
  const span = hi - lo || 1;
  const padX = 4, padY = 4;
  const w = width - padX * 2, h = height - padY * 2;
  const step = values.length > 1 ? w / (values.length - 1) : 0;
  const x = (i: number) => padX + i * step;
  const y = (v: number) => padY + (1 - (v - lo) / span) * h;
  const points = values.map((v, i) => (v == null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`))
    .filter((p): p is string => p != null).join(" ");
  const lastIdx = values.length - 1;
  const last = values[lastIdx];
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className={`tc-spark ${className ?? ""}`} role="img"
         aria-label={`${values.length} points, latest ${last ?? "none"}`}
         preserveAspectRatio="none">
      <polyline points={points} className="tc-spark-line" />
      {values.map((v, i) => v == null ? null : (
        <circle key={i} cx={x(i)} cy={y(v)} r={i === lastIdx ? 3 : 2}
                className={i === lastIdx ? "tc-spark-last" : "tc-spark-pt"}>
          {titles?.[i] && <title>{titles[i]}</title>}
        </circle>
      ))}
    </svg>
  );
}

/** A horizontal bar as a share of `max` — the slowest-15 list. */
export function DurationBar({ value, max, label }: { value: number; max: number; label: string }) {
  const width = max > 0 ? Math.max(1, (100 * value) / max) : 0;
  return (
    <div className="tc-durbar" title={label}>
      <div className="tc-durbar-fill" style={{ width: `${width}%` }} />
      <span className="tc-durbar-text">{label}</span>
    </div>
  );
}

export function Card({ title, aside, children, id, className }: {
  title?: string; aside?: React.ReactNode; children: React.ReactNode; id?: string; className?: string;
}) {
  return (
    <section className={`tc-card ${className ?? ""}`} id={id} aria-label={title}>
      {title && (
        <div className="tc-card-head">
          <h2>{title}</h2>
          {aside && <span className="muted">{aside}</span>}
        </div>
      )}
      {children}
    </section>
  );
}
