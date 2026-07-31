import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";

// Seconds-per-run over time, per agent. Hand-rolled SVG — no chart dependency:
// a scatter of individual runs (click one to open it) with a per-agent daily
// average line, agents toggleable via legend chips.

export type DurationPoint = {
  run_id: string; agent: string; state: string; finished_at: string; seconds: number;
};

// Series colors chosen to pass WCAG AA as text on the dark chip/canvas
// (the legend renders them as text, so contrast gates them like any copy).
const PALETTE = ["#6ea8fe", "#4ade80", "#fbbf24", "#f87171", "#b794f6",
                 "#3ddbe8", "#ef8bb9", "#9aa4b2"];

const W = 860, H = 260, PAD_L = 48, PAD_R = 12, PAD_T = 10, PAD_B = 28;

function niceMax(v: number): number {
  if (v <= 10) return 10;
  const pow = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 5, 10]) if (v <= m * pow) return m * pow;
  return 10 * pow;
}

export default function DurationChart() {
  const navigate = useNavigate();
  const [days, setDays] = useState(14);
  const [points, setPoints] = useState<DurationPoint[]>([]);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [loaded, setLoaded] = useState(false);
  // Log scale by default: the dense every-15-min band lives at 5–20s while
  // outliers reach minutes — linear squashes the band into the axis.
  const [log, setLog] = useState(true);

  useEffect(() => {
    api<DurationPoint[]>(`/api/metrics/durations?days=${days}`)
      .then(setPoints).catch(() => setPoints([]))
      .finally(() => setLoaded(true));
  }, [days]);

  const agents = useMemo(
    () => [...new Set(points.map((p) => p.agent))].sort(), [points]);
  const color = (agent: string) => PALETTE[agents.indexOf(agent) % PALETTE.length];
  const visible = points.filter((p) => !hidden.has(p.agent));

  const now = Date.now();
  const t0 = now - days * 86400_000;
  const yMax = niceMax(Math.max(10, ...visible.map((p) => p.seconds)));
  const x = (t: number) => PAD_L + ((t - t0) / (now - t0)) * (W - PAD_L - PAD_R);
  const frac = (s: number) => log
    ? Math.log10(Math.max(1, s)) / Math.log10(yMax)
    : s / yMax;
  const y = (s: number) => H - PAD_B - frac(s) * (H - PAD_T - PAD_B);
  // grid values: powers of 10 (log) or quarters (linear)
  const gridVals = log
    ? Array.from({ length: Math.floor(Math.log10(yMax)) + 1 }, (_, i) => 10 ** i)
        .concat([yMax]).filter((v, i, a) => a.indexOf(v) === i)
    : [0, 0.25, 0.5, 0.75, 1].map((f) => yMax * f);

  // per-agent daily average polyline
  const avgLines = useMemo(() => {
    const byAgentDay = new Map<string, Map<number, number[]>>();
    for (const p of visible) {
      const day = Math.floor(new Date(p.finished_at).getTime() / 86400_000);
      const m = byAgentDay.get(p.agent) ?? new Map();
      byAgentDay.set(p.agent, m);
      m.set(day, [...(m.get(day) ?? []), p.seconds]);
    }
    return [...byAgentDay.entries()].map(([agent, m]) => ({
      agent,
      pts: [...m.entries()].sort((a, b) => a[0] - b[0]).map(([day, ss]) => ({
        t: day * 86400_000 + 43200_000,   // noon of the day
        s: ss.reduce((a, b) => a + b, 0) / ss.length,
      })),
    }));
  }, [visible]);

  const xticks = useMemo(() => {
    const n = Math.min(days, 7);
    return Array.from({ length: n + 1 }, (_, i) => t0 + ((now - t0) * i) / n);
  }, [days, t0, now]);

  if (loaded && points.length === 0) {
    return <p className="muted">No finished runs in the window.</p>;
  }

  return (
    <div>
      <div className="chip-row" style={{ marginBottom: 6 }}>
        {[7, 14, 30, 90].map((d) => (
          <button key={d} className={d === days ? "chip chip-ok" : "chip"}
                  style={{ cursor: "pointer" }} onClick={() => setDays(d)}>{d}d</button>
        ))}
        <button className="chip" style={{ cursor: "pointer" }} onClick={() => setLog(!log)}>
          {log ? "log" : "linear"} ⇄
        </button>
        <span style={{ width: 12 }} />
        {agents.map((a) => (
          <button key={a} className="chip" style={{
                    cursor: "pointer", opacity: hidden.has(a) ? 0.35 : 1,
                    borderColor: color(a), color: color(a) }}
                  onClick={() => setHidden((h) => {
                    const n = new Set(h); n.has(a) ? n.delete(a) : n.add(a); return n;
                  })}>
            ● {a}
          </button>
        ))}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", maxWidth: W, display: "block" }}>
        {/* y grid + labels */}
        {gridVals.map((v) => (
          <g key={v}>
            <line x1={PAD_L} x2={W - PAD_R} y1={y(v)} y2={y(v)}
                  stroke="currentColor" opacity={0.12} />
            <text x={PAD_L - 6} y={y(v) + 4} textAnchor="end"
                  fontSize={11} fill="currentColor" opacity={0.8}>
              {Math.round(v)}s
            </text>
          </g>
        ))}
        {/* x labels */}
        {xticks.map((t, i) => (
          <text key={i} x={x(t)} y={H - 8} textAnchor="middle"
                fontSize={11} fill="currentColor" opacity={0.8}>
            {new Date(t).toLocaleDateString(undefined, { month: "numeric", day: "numeric" })}
          </text>
        ))}
        {/* daily average lines */}
        {avgLines.map(({ agent, pts }) => pts.length > 1 && (
          <polyline key={agent} fill="none" stroke={color(agent)} strokeWidth={1.5}
                    opacity={0.55}
                    points={pts.map((p) => `${x(p.t)},${y(p.s)}`).join(" ")} />
        ))}
        {/* the runs themselves */}
        {visible.map((p) => (
          <circle key={p.run_id}
                  cx={x(new Date(p.finished_at).getTime())} cy={y(p.seconds)}
                  r={3.2} fill={color(p.agent)}
                  opacity={p.state === "succeeded" ? 0.85 : 0.5}
                  stroke={p.state === "succeeded" ? "none" : "#f85149"}
                  strokeWidth={p.state === "succeeded" ? 0 : 1.5}
                  style={{ cursor: "pointer" }}
                  onClick={() => navigate(`/runs/${p.run_id}`)}>
            <title>{`${p.agent} — ${p.seconds}s (${p.state})\n${new Date(p.finished_at).toLocaleString()}`}</title>
          </circle>
        ))}
      </svg>
      <p className="muted" style={{ fontSize: 12 }}>
        One dot per finished run (red ring = didn't succeed); lines are per-agent daily averages.
        Click a dot to open the run.
      </p>
    </div>
  );
}
