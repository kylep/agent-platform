"""Report HTML sanitization (docs/design/11). Report fragments are
agent-generated from UNTRUSTED inputs (news pages, webhook payloads…), and
they render in the admin's browser — so everything is scrubbed at ingest
through an allow-list, and the viewer adds a script-free sandboxed iframe on
top. Three rules:

1. Structure only — the report-kit's blessed tags plus inline SVG (charts
   from reportcharts). No scripts, no styles, no event handlers, no iframes.
2. Classes are design-system classes — `rk-*` / `ds-*` tokens survive,
   anything else is stripped (mechanical enforcement of "storybook
   components exclusively").
3. Links are http(s); images are data: URIs (the viewer CSP blocks external
   fetches anyway — this keeps stored artifacts honest, not just rendered
   ones).
"""
from __future__ import annotations

import re

import nh3

_HTML_TAGS = {
    "article", "section", "header", "footer", "div", "p", "span", "main",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "code",
    "ul", "ol", "li", "dl", "dt", "dd", "table", "thead", "tbody", "tfoot",
    "tr", "th", "td", "caption", "a", "strong", "em", "b", "i", "u", "s",
    "small", "sub", "sup", "hr", "br", "img", "figure", "figcaption",
    "time", "mark", "abbr",
}
_SVG_TAGS = {
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline",
    "polygon", "text", "tspan", "title", "desc",
}
_GLOBAL_ATTRS = {"class", "id", "title", "role", "aria-label", "aria-hidden"}
_SVG_SHAPE_ATTRS = {
    "viewbox", "viewBox", "width", "height", "x", "y", "x1", "y1", "x2", "y2",
    "cx", "cy", "r", "rx", "ry", "d", "points", "fill", "stroke",
    "stroke-width", "stroke-linejoin", "stroke-linecap", "stroke-dasharray",
    "text-anchor", "font-size", "font-weight", "font-family", "opacity",
    "fill-opacity", "transform", "xmlns",
}

_ATTRIBUTES: dict[str, set[str]] = {"*": set(_GLOBAL_ATTRS)}
for t in _SVG_TAGS:
    _ATTRIBUTES[t] = _SVG_SHAPE_ATTRS | _GLOBAL_ATTRS
# nh3 manages `rel` itself via link_rel; allowing it here is an error.
_ATTRIBUTES["a"] = {"href", "target"} | _GLOBAL_ATTRS
_ATTRIBUTES["img"] = {"src", "alt", "width", "height", "loading"} | _GLOBAL_ATTRS
_ATTRIBUTES["th"] = {"colspan", "rowspan", "scope"} | _GLOBAL_ATTRS
_ATTRIBUTES["td"] = {"colspan", "rowspan"} | _GLOBAL_ATTRS
_ATTRIBUTES["time"] = {"datetime"} | _GLOBAL_ATTRS

# Design-system classes only: rk-* (report-kit composites) and ds-* (token
# utilities). Everything else — tailwind soup, ad-hoc names — is stripped.
_CLASS_TOKEN = re.compile(r"^(rk|ds)-[a-z0-9-]+$")
# fill/stroke: a themed var() or a plain color keyword/none — never url(...)
# paint servers (could reference external content) and never anything exotic.
_PAINT = re.compile(r"^(var\(--ds-[a-z0-9-]+\)|none|currentColor|inherit)$")


def _attr_filter(tag: str, attr: str, value: str) -> str | None:
    if attr == "class":
        kept = [c for c in value.split() if _CLASS_TOKEN.match(c)]
        return " ".join(kept) if kept else None
    if attr in ("fill", "stroke") and not _PAINT.match(value.strip()):
        return None
    return value


def sanitize_report_html(html: str) -> str:
    return nh3.clean(
        html,
        tags=_HTML_TAGS | _SVG_TAGS,
        attributes=_ATTRIBUTES,
        url_schemes={"http", "https", "data", "mailto"},
        link_rel="noopener noreferrer",
        strip_comments=True,
        attribute_filter=_attr_filter,
    )
