"""Text answers for `get` and `outline` (docs/design/46 §3, §6): cut by depth,
by a sibling cursor and by the 32 KiB text cap, so a model never receives a
document dump it cannot read.

Pure functions over a validated document; the API supplies the cap and, for
the outline, the resolved boxes and warnings when a renderer answered.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .errors import UD_NOT_FOUND, UiDesignerError, join_path
from .schema import index_nodes


def size(x: Any) -> int:
    return len(json.dumps(x, separators=(",", ":"), ensure_ascii=False).encode())


def _not_found(what: str) -> UiDesignerError:
    return UiDesignerError(UD_NOT_FOUND, f"{what} not found")


class Cutter:
    """Copies a subtree under a byte budget. A children list stops where the
    budget runs out and says where to resume; a node past the depth keeps its
    own properties and counts its hidden children."""

    def __init__(self, budget: int):
        self.left = budget
        self.notes: list[str] = []
        self.cursor: dict[str, str] | None = None

    def node(self, n: dict, depth: int, after: str | None = None) -> dict:
        out = {k: v for k, v in n.items() if k != "children"}
        self.left -= size(out)
        kids = [k for k in n.get("children") or [] if isinstance(k, dict)]
        if not kids:
            return out
        start = 0
        nid = str(n.get("id", ""))
        if after is not None:
            ids = [k.get("id") for k in kids]
            if after not in ids:
                raise _not_found(f"child {after!r} of {nid!r}")
            start = ids.index(after) + 1
        remaining = len(kids) - start
        if remaining <= 0:
            return out
        if depth <= 0:
            out["children_cut"] = remaining
            self.notes.append(f"{remaining} children of {nid} not shown (depth); get node={nid}")
            return out
        shown: list[dict] = []
        for i in range(start, len(kids)):
            if self.left <= 0 and shown:
                last = str(shown[-1].get("id", ""))
                out["children_more"] = {"count": len(kids) - i, "after": last}
                self.notes.append(f"{len(kids) - i} more children after {last}; "
                                  f"get node={nid} after={last}")
                if self.cursor is None:
                    self.cursor = {"node": nid, "after": last}
                break
            shown.append(self.node(kids[i], depth - 1))
        out["children"] = shown
        return out


TOKENS_NODE = "$tokens"
COMPONENT_SETS_NODE = "$component_sets"
LIBRARIES_NODE = "$libraries"


def _metadata_entries(value: Any, path: list | None = None):
    """Bounded leaf records, including chunks for large literal text defaults."""
    path = path or []
    if isinstance(value, dict) and value:
        for key, child in value.items():
            yield from _metadata_entries(child, path + [key])
    elif isinstance(value, list) and value:
        for index, child in enumerate(value):
            yield from _metadata_entries(child, path + [index])
    elif isinstance(value, str) and len(value.encode()) > 256:
        for offset in range(0, len(value), 64):
            yield {"path": path, "value": value[offset:offset + 64], "offset": offset,
                   "complete": offset + 64 >= len(value)}
    else:
        yield {"path": path, "value": value}


def _component_set_cut(c: Cutter, sets: dict, after: str | None, budget: int, *,
                       sentinel: str = COMPONENT_SETS_NODE, label: str = "component set") -> dict:
    # Small metadata retains the authored shape. Large metadata has an explicit
    # leaf cursor, so even one huge definition/default can be read completely.
    if after is None and size(sets) <= max(0, budget - 384):
        c.left -= size(sets)
        return sets
    entries = list(_metadata_entries(sets))
    start = 0
    if after is not None:
        if len(after) > 12 or not after.isdecimal() or not 0 <= int(after) < len(entries):
            raise _not_found(f"{label} metadata position {after!r}")
        start = int(after) + 1
    shown, used = [], 0
    for index in range(start, len(entries)):
        cost = size(entries[index]) + 1
        if used + cost > max(0, budget - 384):
            if not shown:
                # The caller can retry with its normal text cap; never emit an
                # oversized individual record to make artificial progress.
                c.notes.append(f"{label} metadata needs a larger text budget")
            break
        shown.append(entries[index])
        used += cost
    out = {"_entries": shown, "_format": "path/value; offset is a character offset for chunked strings"}
    end = start + len(shown)
    if end < len(entries):
        cursor = str(end - 1) if end else None
        out["_more"] = {"count": len(entries) - end, "after": cursor}
        c.notes.append(f"more {label} metadata; get node={sentinel}" +
                       (f" after={cursor}" if cursor is not None else ""))
        if c.cursor is None:
            c.cursor = {"node": sentinel}
            if cursor is not None:
                c.cursor["after"] = cursor
    c.left -= size(out)
    return out


def _token_cut(c: Cutter, tokens: dict, after: str | None, budget: int) -> dict:
    """Tokens in order (`group.name`), stopping where `budget` runs out; the
    rest is reached with `node=$tokens after=<group.name>`."""
    flat = [(g, n) for g, grp in tokens.items() if isinstance(grp, dict) for n in grp]
    start = 0
    if after is not None:
        keys = [f"{g}.{n}" for g, n in flat]
        if after not in keys:
            raise _not_found(f"token {after!r}")
        start = keys.index(after) + 1
    out: dict[str, dict] = {}
    used, lastkey = 0, ""
    for i in range(start, len(flat)):
        g, n = flat[i]
        cost = size({n: tokens[g][n]}) + 2
        if used + cost > budget and out:
            rest = len(flat) - i
            out["_more"] = {"count": rest, "after": lastkey}
            c.notes.append(f"{rest} more tokens after {lastkey}; get node={TOKENS_NODE} after={lastkey}")
            if c.cursor is None:
                c.cursor = {"node": TOKENS_NODE, "after": lastkey}
            used += 64
            break
        out.setdefault(g, {})[n] = tokens[g][n]
        used += cost
        lastkey = f"{g}.{n}"
    c.left -= used
    return out


def _cut_siblings(c: Cutter, items: list[tuple[str, dict]], depth: int, budget: int, label: str,
                  into: dict) -> list[dict]:
    """Each (id, node) in turn under `budget` (always at least one); what does
    not fit is counted, and the cursor names the first one left out."""
    outer, c.left = c.left, budget
    shown: list[dict] = []
    for i, (nid, n) in enumerate(items):
        if c.left <= 0 and shown:
            last = str(items[i - 1][0])
            into[f"{label}_more"] = {"count": len(items) - i, "after": last}
            c.notes.append(f"{len(items) - i} more {label} after {last}; get node={nid}")
            if c.cursor is None:
                c.cursor = {"node": nid}
            break
        shown.append(c.node(n, depth))
    consumed = budget - c.left
    c.left = outer - consumed
    return shown


def cut_document(doc: dict, *, node: str | None, depth: int, after: str | None, budget: int) -> dict:
    """`{doc, node, truncated, notes, cursor}` for the whole document or one
    node (page, component root, any node id, or `$tokens`). The whole document
    is cut in order: tokens, then components, then pages (and their leaves);
    whatever is left out sets `truncated` and a cursor."""
    c = Cutter(budget)
    if node == LIBRARIES_NODE:
        body = {"libraries": _component_set_cut(c, doc.get("libraries") or {}, after, budget,
                                               sentinel=LIBRARIES_NODE, label="library")}
    elif node == COMPONENT_SETS_NODE:
        body = {"component_sets": _component_set_cut(c, doc.get("component_sets") or {}, after, budget)}
    elif node == TOKENS_NODE:
        body = {"tokens": _token_cut(c, doc.get("tokens") or {}, after, max(budget, 256))}
    elif node is not None:
        ref = index_nodes(doc).get(node)
        if ref is None:
            raise _not_found(f"node {node!r}")
        body = c.node(ref.node, depth, after)
    else:
        if after is not None:
            raise _not_found("a cursor needs `node`")
        body = {k: v for k, v in doc.items() if k not in ("pages", "components", "tokens", "component_sets", "libraries")}
        c.left -= size(body)
        if doc.get("libraries"):
            share = max(0, c.left // 4)
            if size(doc["libraries"]) <= share:
                body["libraries"] = _component_set_cut(c, doc["libraries"], None, share + 384,
                                                       sentinel=LIBRARIES_NODE, label="library")
            else:
                body["libraries"] = {"_cut": True, "count": len(doc["libraries"])}
                c.left -= size(body["libraries"])
                c.notes.append(f"libraries omitted (budget); get node={LIBRARIES_NODE}")
                c.cursor = {"node": LIBRARIES_NODE}
        if doc.get("component_sets"):
            share = max(0, c.left // 4)
            if size(doc["component_sets"]) <= share:
                body["component_sets"] = _component_set_cut(c, doc["component_sets"], None, share + 384)
            else:
                body["component_sets"] = {"_cut": True, "count": len(doc["component_sets"])}
                c.left -= size(body["component_sets"])
                c.notes.append(f"component sets omitted (budget); get node={COMPONENT_SETS_NODE}")
                if c.cursor is None:
                    c.cursor = {"node": COMPONENT_SETS_NODE}
        if doc.get("tokens"):
            body["tokens"] = _token_cut(c, doc["tokens"], None, max(c.left // 4, 512))
        comps = [(k, v) for k, v in (doc.get("components") or {}).items()]
        pages = [(str(p.get("id", "")), p) for p in doc.get("pages") or []]
        cb = max(c.left // 2, 512) if pages else max(c.left, 512)
        shown = _cut_siblings(c, comps, depth, cb, "components", body)
        body["components"] = {k: n for (k, _), n in zip(comps, shown)}
        body["pages"] = _cut_siblings(c, pages, depth, max(c.left, 512), "pages", body)
    return {"doc": body, "node": node, "truncated": bool(c.notes), "notes": c.notes, "cursor": c.cursor}


# --- outline ----------------------------------------------------------------

def _dim(v: Any) -> str:
    if isinstance(v, bool) or v is None:
        return "?"
    if isinstance(v, (int, float)):
        return f"{v:g}"
    return str(v)


def _layout(n: dict) -> str:
    lay = n.get("layout")
    if not isinstance(lay, dict):
        return ""
    bits = ["row" if lay.get("dir") == "row" else "col"]
    for k in ("gap", "pad", "align", "wrap"):
        if lay.get(k) not in (None, False):
            bits.append(f"{k}={lay[k]}".replace(" ", ""))
    return " [" + " ".join(bits) + "]"


def _line(n: dict, depth: int, boxes: dict[str, tuple[float, float]], warns: list[str]) -> str:
    nid = str(n.get("id", ""))
    kind = n.get("type", "page")
    bits = [nid, kind]
    if kind == "instance":
        bits.append(f"of={n.get('of')}")
    label = n.get("title")
    if not label and kind == "text" and isinstance(n.get("text"), str):
        label = n["text"].replace("\n", " ")
        label = label[:30] + ("…" if len(label) > 30 else "")
    if label:
        bits.append(json.dumps(label, ensure_ascii=False))
    box = boxes.get(nid)
    if box is not None:
        bits.append(f"{box[0]:g}x{box[1]:g}")
    elif "w" in n or "h" in n:
        bits.append(f"{_dim(n.get('w'))}x{_dim(n.get('h'))}")
    line = "  " * depth + " ".join(bits) + _layout(n)
    if warns:
        line += " [!" + ",".join(warns) + "]"
    return line


def outline_lines(doc: dict, *, page: str | None = None, node: str | None = None,
                  boxes: dict[str, tuple[float, float]] | None = None,
                  warnings: list[dict] | None = None) -> list[tuple[str, str]]:
    """`[(node id, line)]` depth first. `warnings` carry `id` (layout) or only
    `path` (lint); both end up on the line of the node they are about."""
    boxes = boxes or {}
    by_id: dict[str, list[str]] = {}
    by_path: dict[str, list[str]] = {}
    for w in warnings or []:
        code = str(w.get("code", "")).removeprefix("UD-WARN-").lower()
        if w.get("id"):
            by_id.setdefault(str(w["id"]), []).append(code)
        elif w.get("path"):
            by_path.setdefault(str(w["path"]), []).append(code)
    out: list[tuple[str, str]] = []

    def walk(n: dict, depth: int, path: str) -> None:
        nid = str(n.get("id", ""))
        warns = sorted(set(by_id.get(nid, []) + by_path.get(path, [])))
        out.append((nid, _line(n, depth, boxes, warns)))
        for i, k in enumerate(n.get("children") or []):
            if isinstance(k, dict):
                walk(k, depth + 1, join_path(path, "children", i))

    if node is not None:
        ref = index_nodes(doc).get(node)
        if ref is None:
            raise _not_found(f"node {node!r}")
        walk(ref.node, 0, "")
        return out
    pages = doc.get("pages") or []
    if page is not None and not any(p.get("id") == page for p in pages):
        raise _not_found(f"page {page!r}")
    for pi, p in enumerate(pages):
        if page is not None and p.get("id") != page:
            continue
        out.append((str(p.get("id", "")), _line({**p, "type": "page"}, 0, {}, [])))
        for ci, k in enumerate(p.get("children") or []):
            if isinstance(k, dict):
                walk(k, 1, join_path("$.pages", pi, "children", ci))
    if page is None:
        for key, root in (doc.get("components") or {}).items():
            cpath = join_path("$.components", key)
            out.append((key, "component " + _line(root, 0, boxes, by_path.get(cpath, []))))
            for ci, k in enumerate(root.get("children") or []):
                if isinstance(k, dict):
                    walk(k, 1, join_path(cpath, "children", ci))
    return out


def cut_lines(lines: list[tuple[str, str]], *, after: str | None, cap: int) -> tuple[str, str | None]:
    """The lines after `after` that fit in `cap` bytes, and the id to resume
    from (None when everything fit)."""
    start = 0
    if after is not None:
        for i, (nid, _l) in enumerate(lines):
            if nid == after:
                start = i + 1
                break
        else:
            raise _not_found(f"outline position {after!r}")
    out: list[str] = []
    used = 0
    last: str | None = None
    for i in range(start, len(lines)):
        nid, line = lines[i]
        n = len(line.encode()) + 1
        if used + n > cap - 160 and out:
            out.append(f"… {len(lines) - i} more lines; outline after={last}")
            return "\n".join(out), last
        out.append(line)
        used += n
        last = nid
    return "\n".join(out), None


# --- inspect (design 46 section 3) ------------------------------------------------

_STYLE_KEYS = ("fill", "color", "stroke", "radius", "opacity", "shadow", "style", "rotate", "clip")
_TOKEN_RE = re.compile(r"\$(?:color|space|radius|type)\.[a-z0-9_-]+", re.I)


def _own(node: dict) -> dict:
    return {k: v for k, v in node.items() if k != "children"}


def inspect_node(doc: dict, node_id: str, trees: dict[str | None, dict], *, budget: int = 32768) -> dict:
    """What an agent needs to reason about one node: its resolved box and its
    children's, the resolved colours and typography per mode, the tokens its own
    properties name, its text and the component it instances. `trees` maps a mode
    (None when the document has none) to that mode's resolved tree."""
    from . import svg as _svg
    ref = index_nodes(doc).get(node_id)
    authored = _own(ref.node) if ref is not None else None
    modes: dict[str, dict] = {}
    geometry: dict | None = None
    text: str | None = None
    node_type = authored.get("type") if authored else None
    warnings: list[dict] = []
    for mode, tree in trees.items():
        found, _anc = _svg.find_node(tree, node_id)
        node_type = node_type or found.get("type")
        lines = ((found.get("text") or {}).get("lines") or []) if isinstance(found.get("text"), dict) else []
        if geometry is None:
            geometry = _svg.boxes(found)
            if lines:
                text = "\n".join(str(ln.get("text", "")) for ln in lines)
        look = {k: found[k] for k in _STYLE_KEYS if k in found}
        runs = [r for ln in lines for r in ln.get("runs") or []]
        if runs:                       # a text node's colour and face live on its runs
            look.setdefault("color", runs[0].get("color"))
            look["typography"] = [{"face": r.get("face"), "size": r.get("size")}
                                  for r in {(r.get("face"), r.get("size")): r for r in runs}.values()]
        modes[mode or "default"] = look
        if not warnings:
            warnings = [w for w in tree.get("warnings") or [] if w.get("id") == node_id]
    if authored is None and geometry is None:
        raise _not_found(f"node {node_id!r}")
    own = json.dumps(authored or {}, ensure_ascii=False)
    tokens = sorted({t.lower() for t in _TOKEN_RE.findall(own)})
    if authored and ("props" in authored or "variant" in authored or
                     authored.get("of") in (doc.get("component_sets") or {})):
        from .schema import iter_token_refs
        tokens = sorted({ref.lower() for _, ref in iter_token_refs({"node": authored})})
    if authored and isinstance(authored.get("text"), str):
        text = authored["text"]
    elif authored and isinstance(authored.get("text"), list):
        text = "".join(str(r.get("text", "")) for r in authored["text"] if isinstance(r, dict))
    answer = {"id": node_id, "type": node_type, "title": (authored or {}).get("title"),
            "geometry": geometry, "modes": modes, "tokens": tokens, "text": text,
            "component": (authored or {}).get("of") if node_type == "instance" else None,
            "authored": authored, "warnings": warnings}
    if authored and authored.get("of") in (doc.get("component_sets") or {}):
        from .components import describe_instance
        details = describe_instance(doc, authored)
        if size(details) > max(0, budget - size(answer) - 512):
            details = {"component_set": authored["of"], "axes": details.get("axes", {}),
                       "variant": details.get("variant", {}),
                       "_cut": True, "get": {"node": COMPONENT_SETS_NODE}}
        answer["component_set"] = details
        from .platform_library import inspect_instance
        implementation = inspect_instance(doc, authored)
        if implementation is not None:
            if size(implementation) > max(0, budget - size(answer)):
                implementation = {**implementation, "props": {}, "fixture": {}, "snippet": None}
            answer["implementation"] = implementation
    return answer


# --- diff -------------------------------------------------------------------

def _op_summary(op: dict) -> str:
    name = str(op.get("op", "?"))
    target = op.get("id") or op.get("ids") or op.get("parent") or op.get("from")
    if isinstance(target, list):
        target = ",".join(str(t) for t in target[:6]) + ("…" if len(target) > 6 else "")
    keys = ""
    if isinstance(op.get("set"), dict) and name in ("update", "tokens", "component_sets"):
        keys = " {" + ",".join(list(op["set"])[:6]) + "}"
    node = op.get("node")
    added = f" +{node.get('id')}" if isinstance(node, dict) and node.get("id") else ""
    return f"{name}({target}){keys}{added}" if target else f"{name}{keys}{added}"


def diff_text(chunks: list[dict], *, cap: int) -> tuple[str, bool]:
    """One line per write: `v8 reason: op(target) …`, cut at `cap` bytes."""
    out: list[str] = []
    used = 0
    for i, c in enumerate(chunks):
        ops = [o for o in c.get("ops") or [] if isinstance(o, dict)]
        line = f"v{c.get('version')} {str(c.get('reason') or '')[:120]}: " + \
               ", ".join(_op_summary(o) for o in ops[:12]) + (f" … {len(ops) - 12} more" if len(ops) > 12 else "")
        n = len(line.encode()) + 1
        if used + n > cap - 128 and out:
            out.append(f"… {len(chunks) - i} more writes; narrow from/to")
            return "\n".join(out), True
        out.append(line)
        used += n
    return "\n".join(out), False
