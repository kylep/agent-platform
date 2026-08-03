"""Server-rendered SVG charts for reports (docs/design/11). Reports are
static, script-free HTML — the viewer iframe has no JavaScript — so charts
are inline SVG generated here and embedded by the authoring agent.

Colors are CSS custom properties (var(--ds-chart-N)): the SVG inherits the
design-system palette (and the viewer's light/dark theme) from the report-kit
shell it renders inside. Every chart kind here has a matching Storybook story
under ReportKit/ — new kinds land in Storybook first (Kyle's rule).

No third-party plotting deps: the output surface must stay inside the
sanitizer's SVG allow-list, and hand-rolled SVG keeps it exact."""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

KINDS = ("bar", "line", "sparkline", "donut")

# Palette cycle — tokens.css defines --ds-chart-1..8.
def _color(i: int) -> str:
    return f"var(--ds-chart-{(i % 8) + 1})"


class ChartSeries(BaseModel):
    label: str = ""
    values: list[float]


class ChartSpec(BaseModel):
    kind: str
    series: list[ChartSeries] = Field(min_length=1)
    labels: list[str] = []          # x-axis / slice labels
    title: str = ""
    width: int = Field(640, ge=120, le=1600)
    height: int = Field(240, ge=24, le=900)   # sparklines run small

    @model_validator(mode="after")
    def _valid(self):
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        if self.kind == "donut" and len(self.series) != 1:
            raise ValueError("donut takes exactly one series")
        if self.kind == "sparkline" and len(self.series) != 1:
            raise ValueError("sparkline takes exactly one series")
        if not any(s.values for s in self.series):
            raise ValueError("series values must not be empty")
        return self


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def render_chart(spec: ChartSpec) -> str:
    if spec.kind == "donut":
        return _donut(spec)
    if spec.kind == "sparkline":
        return _sparkline(spec)
    if spec.kind == "line":
        return _line(spec)
    return _bar(spec)


# --- shared frame -------------------------------------------------------------

_TEXT = 'font-family="inherit" fill="var(--ds-text-muted)" font-size="11"'
PAD_L, PAD_R, PAD_T, PAD_B = 36, 8, 8, 22


def _frame(spec: ChartSpec, body: str, pad_t: int = PAD_T) -> str:
    title = (f'<text x="{PAD_L}" y="14" {_TEXT} font-size="12" '
             f'font-weight="600">{_esc(spec.title)}</text>') if spec.title else ""
    return (f'<svg role="img" class="rk-chart" viewBox="0 0 {spec.width} {spec.height}" '
            f'width="{spec.width}" height="{spec.height}" '
            f'xmlns="http://www.w3.org/2000/svg">{title}{body}</svg>')


def _scale(values: list[float]) -> tuple[float, float]:
    lo, hi = min(values + [0.0]), max(values + [0.0])
    if hi == lo:
        hi = lo + 1.0
    return lo, hi


def _plot_rect(spec: ChartSpec) -> tuple[float, float, float, float]:
    top = PAD_T + (18 if spec.title else 0)
    return (PAD_L, top, spec.width - PAD_L - PAD_R, spec.height - top - PAD_B)


def _axis(spec: ChartSpec, lo: float, hi: float) -> str:
    x, y, w, h = _plot_rect(spec)
    out = [f'<line x1="{x}" y1="{y + h}" x2="{x + w}" y2="{y + h}" '
           f'stroke="var(--ds-border)" stroke-width="1"/>']
    for frac, val in ((0.0, hi), (1.0, lo)):
        yy = y + h * frac
        label = f"{val:g}"
        out.append(f'<text x="{x - 6}" y="{yy + 4}" text-anchor="end" {_TEXT}>{_esc(label)}</text>')
    step = max(1, len(spec.labels) // 8) if spec.labels else 1
    for i, lab in enumerate(spec.labels):
        if i % step:
            continue
        xx = x + (i + 0.5) * (w / max(1, len(spec.labels)))
        out.append(f'<text x="{xx}" y="{y + h + 14}" text-anchor="middle" {_TEXT}>{_esc(lab)}</text>')
    return "".join(out)


def _legend(spec: ChartSpec) -> str:
    named = [s for s in spec.series if s.label]
    if len(named) < 2:
        return ""
    x0, y0, _, _ = _plot_rect(spec)
    parts, xx = [], x0
    for i, s in enumerate(spec.series):
        parts.append(f'<rect x="{xx}" y="{y0 - 6}" width="8" height="8" rx="2" fill="{_color(i)}"/>')
        parts.append(f'<text x="{xx + 12}" y="{y0 + 2}" {_TEXT}>{_esc(s.label)}</text>')
        xx += 12 + 7 * len(s.label) + 16
    return "".join(parts)


# --- kinds --------------------------------------------------------------------

def _bar(spec: ChartSpec) -> str:
    x, y, w, h = _plot_rect(spec)
    all_vals = [v for s in spec.series for v in s.values]
    lo, hi = _scale(all_vals)
    n = max(len(s.values) for s in spec.series)
    group_w = w / max(1, n)
    bar_w = max(2.0, group_w * 0.8 / len(spec.series))
    bars = []
    for si, s in enumerate(spec.series):
        for i, v in enumerate(s.values):
            bh = (v - min(lo, 0)) / (hi - min(lo, 0)) * h if hi else 0
            bx = x + i * group_w + group_w * 0.1 + si * bar_w
            bars.append(f'<rect x="{bx:.1f}" y="{y + h - bh:.1f}" width="{bar_w:.1f}" '
                        f'height="{bh:.1f}" rx="2" fill="{_color(si)}"/>')
    return _frame(spec, _axis(spec, min(lo, 0), hi) + "".join(bars) + _legend(spec))


def _points(values: list[float], rect, lo, hi) -> list[tuple[float, float]]:
    x, y, w, h = rect
    n = max(1, len(values) - 1)
    return [(x + i * (w / n), y + h - (v - lo) / (hi - lo) * h)
            for i, v in enumerate(values)]


def _line(spec: ChartSpec) -> str:
    rect = _plot_rect(spec)
    all_vals = [v for s in spec.series for v in s.values]
    lo, hi = _scale(all_vals)
    parts = [_axis(spec, lo, hi)]
    for si, s in enumerate(spec.series):
        pts = _points(s.values, rect, lo, hi)
        d = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        parts.append(f'<polyline points="{d}" fill="none" stroke="{_color(si)}" '
                     f'stroke-width="2" stroke-linejoin="round"/>')
    return _frame(spec, "".join(parts) + _legend(spec))


def _sparkline(spec: ChartSpec) -> str:
    s = spec.series[0]
    lo, hi = _scale(s.values)
    rect = (2.0, 2.0, spec.width - 4.0, spec.height - 4.0)
    pts = _points(s.values, rect, lo, hi)
    d = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
    lx, ly = pts[-1]
    body = (f'<polyline points="{d}" fill="none" stroke="{_color(0)}" stroke-width="1.5"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.5" fill="{_color(0)}"/>')
    return (f'<svg role="img" class="rk-sparkline" viewBox="0 0 {spec.width} {spec.height}" '
            f'width="{spec.width}" height="{spec.height}" '
            f'xmlns="http://www.w3.org/2000/svg">{body}</svg>')


def _donut(spec: ChartSpec) -> str:
    import math
    s = spec.series[0]
    total = sum(v for v in s.values if v > 0) or 1.0
    cx, cy = spec.width / 2, (spec.height + (18 if spec.title else 0)) / 2
    r = min(spec.width, spec.height - (18 if spec.title else 0)) / 2 - 10
    r_in = r * 0.6
    parts, angle = [], -math.pi / 2
    for i, v in enumerate(v for v in s.values):
        if v <= 0:
            continue
        frac = v / total
        a2 = angle + frac * 2 * math.pi
        large = 1 if frac > 0.5 else 0
        x1, y1 = cx + r * math.cos(angle), cy + r * math.sin(angle)
        x2, y2 = cx + r * math.cos(a2), cy + r * math.sin(a2)
        x3, y3 = cx + r_in * math.cos(a2), cy + r_in * math.sin(a2)
        x4, y4 = cx + r_in * math.cos(angle), cy + r_in * math.sin(angle)
        parts.append(
            f'<path d="M {x1:.1f} {y1:.1f} A {r:.1f} {r:.1f} 0 {large} 1 {x2:.1f} {y2:.1f} '
            f'L {x3:.1f} {y3:.1f} A {r_in:.1f} {r_in:.1f} 0 {large} 0 {x4:.1f} {y4:.1f} Z" '
            f'fill="{_color(i)}"/>')
        angle = a2
    # slice labels as a side legend
    ly = 20 + (18 if spec.title else 0)
    for i, lab in enumerate(spec.labels[:len(s.values)]):
        parts.append(f'<rect x="8" y="{ly - 8}" width="8" height="8" rx="2" fill="{_color(i)}"/>')
        parts.append(f'<text x="20" y="{ly}" {_TEXT}>{_esc(lab)} ({s.values[i]:g})</text>')
        ly += 16
    return _frame(spec, "".join(parts))
