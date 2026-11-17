"""Ops (docs/design/46 §2.3): one `edit` call is one batch, applied atomically
to a copy of the document and validated as a whole afterwards. Pure; the store
(T3) persists the returned document and version.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from agentplatform.ui_designer.errors import (UD_VERSION_CONFLICT, Issue,
                                              UiDesignerError, invalid, issue, join_path)
from agentplatform.ui_designer.schema import (ID_RE, NodeRef, assign_ids, index_nodes, iter_token_refs, lint,
                                              normalize_document, subtree_ids, validate_document)

MAX_OPS = 500
MAX_RESIZE_UNDO_OPS = 128
MAX_RESIZE_UNDO_BYTES = 8 << 10

OP_FIELDS: dict[str, tuple[set[str], set[str]]] = {  # op -> (required, optional)
    "insert": ({"parent", "node"}, {"index"}),
    "update": ({"id", "set"}, set()),
    "resize": ({"id", "set"}, set()),
    "boolean": ({"kind", "ids"}, set()),
    "icon": ({"icon", "revision", "parent", "id"}, {"index", "size", "color", "stroke_width", "x", "y"}),
    "remove": ({"ids"}, set()),
    "move": ({"id", "parent"}, {"index"}),
    "replace": ({"id", "node"}, set()),
    "set_text": ({"id", "text"}, set()),
    "tokens": ({"set"}, set()),
    "component_sets": ({"set"}, set()),
    "define_component": ({"id", "from"}, set()),
    "set_doc": ({"doc"}, set()),
}
# What `define_component` leaves on the instance that replaces the node: its
# place in the parent, not its look.
_PLACEMENT = ("title", "w", "h", "x", "y", "absolute", "rotate",
              "min_w", "max_w", "min_h", "max_h", "constraints")
_NOT_UPDATABLE = {"id", "type", "children"}


@dataclass
class ApplyResult:
    doc: dict
    version: int
    ops: list
    warnings: list[dict] = field(default_factory=list)
    undo_ops: list[dict] | None = None


def apply_ops(doc: dict, ops: Any, *, version: int, expected_version: int | None,
              ops_since: Sequence | Callable[[int], Sequence] | None = None,
              known_assets: set[str] | None = None, known_fonts: dict[str, dict] | None = None) -> ApplyResult:
    """Apply a batch to `doc` (never mutated) at `version`.

    CAS: `expected_version` must equal `version`; otherwise UD-VERSION-CONFLICT
    answers with the current version and the ops applied since (`ops_since` is
    a sequence, or a callable taking the expected version, so the store reads
    history only on a conflict). Any failing op, or an invalid result, raises
    and nothing is returned. Success is exactly one version: `version + 1`.
    """
    if expected_version is None or isinstance(expected_version, bool) or \
            not isinstance(expected_version, int):
        raise invalid([issue("UD-MISSING", "$.expected_version",
                             "writes carry the integer `expected_version` they were based on")])
    if expected_version != version:
        since = ops_since(expected_version) if callable(ops_since) else (ops_since or [])
        raise UiDesignerError(UD_VERSION_CONFLICT, f"document is at version {version}, "
                              f"not {expected_version}",
                              detail={"current_version": version, "ops_since": list(since)})
    if not isinstance(ops, list) or not ops:
        raise invalid([issue("UD-TYPE", "$.ops", "expected a non-empty list of ops", ops)])
    if len(ops) > MAX_OPS:
        raise invalid([issue("UD-LIMIT", "$.ops", f"at most {MAX_OPS} ops per batch")])
    resizing = any(isinstance(op, dict) and op.get("op") == "resize" for op in ops)
    if resizing and len(ops) != 1:
        raise invalid(
            [issue("UD-LIMIT", "$.ops", "resize must be the only op in its edit batch")]
        )
    work = copy.deepcopy(doc)
    captured = {}
    for i, op in enumerate(ops):
        _validate_explicit_paths(op)
        work = _apply_one(
            work, op, join_path("$.ops", i), captured if resizing else None
        )
    validate_document(work, known_assets=known_assets, known_fonts=known_fonts)
    return ApplyResult(
        doc=work,
        version=version + 1,
        ops=captured.get("forward", copy.deepcopy(ops)),
        warnings=lint(work),
        undo_ops=captured.get("undo"),
    )


def _validate_explicit_paths(op: Any) -> None:
    """Strictly check newly authored path data without revalidating legacy restores."""
    if not isinstance(op, dict):
        return
    from .vector_geometry import validate_path

    def fields(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if "d" in node and node["d"] is not None:
            validate_path(node["d"])
        overrides = node.get("overrides")
        if isinstance(overrides, dict):
            for key, value in overrides.items():
                if value is not None and isinstance(key, str) and (key == "d" or key.endswith(".d")):
                    validate_path(value)

    if op.get("op") in ("insert", "replace"):
        def subtree(node: Any) -> None:
            fields(node)
            if isinstance(node, dict) and isinstance(node.get("children"), list):
                for child in node["children"]:
                    subtree(child)
        subtree(op.get("node"))
    elif op.get("op") == "update":
        fields(op.get("set"))


def _fail(code: str, path: str, message: str, value: Any = None) -> UiDesignerError:
    return invalid([issue(code, path, message, value)])


def _apply_one(doc: dict, op: Any, path: str, resize_capture: dict | None = None) -> dict:
    if not isinstance(op, dict):
        raise _fail("UD-TYPE", path, "an op is an object", op)
    name = op.get("op")
    if name not in OP_FIELDS:
        raise _fail("UD-OP-UNKNOWN", join_path(path, "op"), f"unknown op {name!r}", name)
    required, optional = OP_FIELDS[name]
    problems: list[Issue] = []
    for k in op:
        if k != "op" and k not in required | optional:
            problems.append(issue("UD-OP-FIELD", join_path(path, k), f"`{name}` has no field {k!r}"))
    for k in sorted(required):
        if k not in op:
            problems.append(issue("UD-MISSING", join_path(path, k), f"`{name}` needs `{k}`"))
    if problems:
        raise invalid(problems)
    if name == "resize":
        return _resize(doc, op, path, resize_capture)
    if name in ("boolean", "icon"):
        raise _fail("UD-OP-FIELD", path, "boolean/icon operations must be prepared by the edit API")
    return _OPS[name](doc, op, path)


def _target(doc: dict, id_: Any, path: str, kinds: tuple[str, ...], what: str = "id") -> NodeRef:
    ref = index_nodes(doc).get(id_) if isinstance(id_, str) else None
    if ref is None:
        raise _fail("UD-OP-TARGET", path, f"no {what} {id_!r}", id_)
    if ref.kind not in kinds:
        raise _fail("UD-OP-TARGET", path, f"{id_!r} is a {ref.kind}; this op needs a "
                    f"{' or '.join(kinds)}", id_)
    return ref


def _container(ref: NodeRef, path: str) -> list:
    """The children list of a page or frame (created when a frame has none)."""
    if ref.kind != "page" and ref.node.get("type") != "frame":
        raise _fail("UD-OP-TARGET", path, f"{ref.node.get('id')!r} cannot hold children "
                    f"(type {ref.node.get('type')!r})", ref.node.get("id"))
    return ref.node.setdefault("children", [])


def _taken(doc: dict) -> set[str]:
    return set(index_nodes(doc)) | set(doc.get("component_sets") or {})


def _index(idx: Any, n: int, path: str) -> int:
    if idx is None:
        return n
    if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx <= n:
        raise _fail("UD-OP-INDEX", path, f"index must be an integer from 0 to {n}", idx)
    return idx


def _node_arg(node: Any, path: str) -> dict:
    if not isinstance(node, dict):
        raise _fail("UD-TYPE", path, "expected a node object", node)
    return copy.deepcopy(node)


def _insert(doc: dict, op: dict, path: str) -> dict:
    parent = _target(doc, op["parent"], join_path(path, "parent"), ("page", "node", "component"))
    kids = _container(parent, join_path(path, "parent"))
    node = _node_arg(op["node"], join_path(path, "node"))
    assign_ids(node, _taken(doc))
    kids.insert(_index(op.get("index"), len(kids), join_path(path, "index")), node)
    return doc


def _update(doc: dict, op: dict, path: str) -> dict:
    ref = _target(doc, op["id"], join_path(path, "id"), ("page", "node", "component"))
    changes = op["set"]
    if not isinstance(changes, dict):
        raise _fail("UD-TYPE", join_path(path, "set"), "expected an object", changes)
    for k, v in changes.items():
        kp = join_path(path, "set", k)
        if k in _NOT_UPDATABLE:
            raise _fail("UD-OP-FIELD", kp, f"`update` cannot set {k!r}; use "
                        f"{'insert/remove/move' if k == 'children' else 'replace'}")
        if ref.kind == "page" and k != "title":
            raise _fail("UD-OP-FIELD", kp, "a page's only updatable key is `title`")
        if v is None:
            ref.node.pop(k, None)
        else:
            ref.node[k] = copy.deepcopy(v)
    return doc


def _resize(doc: dict, op: dict, path: str, captured: dict | None) -> dict:
    from .constraints import effective_size, has_pins, resize_children
    from .components import nodes

    ref = _target(doc, op["id"], join_path(path, "id"), ("node", "component"))
    changes = op["set"]
    dimensions = {"w", "h", "min_w", "max_w", "min_h", "max_h"}
    if not isinstance(changes, dict) or not dimensions.intersection(changes):
        raise _fail(
            "UD-OP-FIELD", path, "resize needs a dimension or min/max bound in set"
        )
    original = copy.deepcopy(ref.node)
    original_geometry = {
        n["id"]: {
            key: copy.deepcopy(n[key]) for key in ("x", "y", "w", "h") if key in n
        }
        for n in nodes(original)
    }
    doc = _update(doc, op, path)
    # Untrusted parent fields must be validated before helper numeric conversions.
    validate_document(doc)
    updated = index_nodes(doc)[op["id"]].node
    before, after = effective_size(original), effective_size(updated)
    for index, (axis, extent) in enumerate((("horizontal", "w"), ("vertical", "h"))):
        if (
            original.get(extent) != updated.get(extent)
            and (before[index] is None or after[index] is None)
            and has_pins(updated, axis)
        ):
            raise _fail(
                "UD-VALUE", path, "active pins require fixed numeric parent dimensions"
            )
    result = resize_children(updated, before, after)
    updated.clear()
    updated.update(result)
    forward = [{"op": "update", "id": op["id"], "set": copy.deepcopy(changes)}]
    inverse = [
        {
            "op": "update",
            "id": op["id"],
            "set": {key: copy.deepcopy(original.get(key)) for key in changes},
        }
    ]
    changed_instances = []
    for node in nodes(updated):
        if node["id"] == op["id"]:
            if node.get("type") == "instance":
                changed_instances.append(node)
            continue
        old = original_geometry[node["id"]]
        patch = {
            key: copy.deepcopy(node.get(key))
            for key in ("x", "y", "w", "h")
            if (key in old) != (key in node) or old.get(key) != node.get(key)
        }
        if patch:
            forward.append({"op": "update", "id": node["id"], "set": patch})
            inverse.append(
                {
                    "op": "update",
                    "id": node["id"],
                    "set": {key: copy.deepcopy(old.get(key)) for key in patch},
                }
            )
            if node.get("type") == "instance":
                changed_instances.append(node)
    # Instance expansion is non-mutating and can resolve each declared mode.
    if changed_instances:
        from .constraints import validate_instance_resize

        for instance in changed_instances:
            validate_instance_resize(doc, instance)
    if (
        len(inverse) > MAX_RESIZE_UNDO_OPS
        or len(json.dumps(inverse, ensure_ascii=False, separators=(",", ":")).encode())
        > MAX_RESIZE_UNDO_BYTES
    ):
        raise _fail(
            "UD-LIMIT",
            path,
            "resize undo exceeds 128 literal updates or 8192 bytes; resize a smaller subtree",
        )
    if captured is not None:
        captured.update(forward=forward, undo=inverse)
    return doc


def prepare_boolean(doc: dict, op: dict) -> dict:
    """Validate an ordered authored selection and exact inverse before native work."""
    from .constraints import effective_size
    path = "$.ops[0]"
    if not isinstance(op, dict) or set(op) != {"op", "kind", "ids"} or op.get("op") != "boolean":
        raise _fail("UD-OP-FIELD", path, "boolean is {op, kind, ids}")
    if op["kind"] not in ("union", "subtract", "intersect", "exclude"):
        raise _fail("UD-VALUE", path + ".kind", "kind must be union, subtract, intersect or exclude")
    ids = op["ids"]
    if not (isinstance(ids, list) and 2 <= len(ids) <= 8 and all(isinstance(value, str) and ID_RE.fullmatch(value) for value in ids)
            and len(set(ids)) == len(ids)):
        raise _fail("UD-LIMIT", path + ".ids", "choose 2 to 8 distinct authored siblings in operation order")
    refs = [_target(doc, value, path + ".ids", ("node",)) for value in ids]
    first = refs[0]
    if any(ref.parent != first.parent or ref.siblings is not first.siblings for ref in refs):
        raise _fail("UD-OP-TARGET", path, "boolean operands must share one authored sibling list")
    container = _target(doc, first.parent, path, ("page", "node", "component")).node
    if container.get("layout") and not all(ref.node.get("absolute") is True for ref in refs):
        raise _fail("UD-VALUE", path, "auto-layout operands must all be absolute; otherwise use free-positioned siblings")
    if container.get("mask") and container.get("children") and container["children"][0]["id"] in ids:
        raise _fail("UD-VALUE", path, "the first mask child cannot be a boolean operand")
    if isinstance(first.node.get("stroke"), dict) and "sides" in first.node["stroke"]:
        raise _fail("UD-VALUE", path, "convert the first operand's per-side stroke to a uniform width before boolean editing")
    operands = []
    for ref in refs:
        node = ref.node
        if node.get("type") not in ("path", "rect", "ellipse"):
            raise _fail("UD-VALUE", path, "boolean operands must be paths, rectangles or ellipses")
        for key in ("x", "y", "w", "h", "rotate", "radius", "min_w", "max_w", "min_h", "max_h"):
            values = node.get(key)
            values = values if isinstance(values, list) else [values]
            if any(isinstance(value, str) for value in values if value is not None):
                raise _fail("UD-VALUE", path, "boolean geometry must use fixed numeric values, not token or intrinsic geometry")
        width, height = effective_size(node)
        empty_path = node["type"] == "path" and not node.get("d")
        if width is None or height is None or width < 0 or height < 0 or (not empty_path and (width == 0 or height == 0)):
            raise _fail("UD-VALUE", path, "boolean operands need fixed positive dimensions (empty paths may be zero-sized)")
        operand = {"type": node["type"], "x": node.get("x", 0), "y": node.get("y", 0),
                   "w": width, "h": height}
        for key in ("rotate", "radius", "d"):
            if key in node:
                operand[key] = copy.deepcopy(node[key])
        operands.append(operand)
    indices = {node["id"]: index for index, node in enumerate(first.siblings)}
    undo = [{"op": "replace", "id": ids[0], "node": copy.deepcopy(first.node)}]
    undo.extend({"op": "insert", "parent": first.parent, "node": copy.deepcopy(ref.node), "index": indices[ref.node["id"]]}
                for ref in sorted(refs[1:], key=lambda ref: indices[ref.node["id"]]))
    if len(json.dumps(undo, ensure_ascii=False, separators=(",", ":")).encode()) > MAX_RESIZE_UNDO_BYTES:
        raise _fail("UD-LIMIT", path, "boolean exact undo exceeds the 8192-byte budget; simplify the selection")
    if len(json.dumps(operands, separators=(",", ":")).encode()) > 64 << 10:
        raise _fail("UD-LIMIT", path, "boolean geometry input exceeds 65536 bytes")
    return {"operands": operands, "undo_ops": undo, "first": copy.deepcopy(first.node), "ids": ids}


def boolean_literal_ops(prepared: dict, geometry: dict) -> list[dict]:
    """Geometry is worker output; history always contains ordinary literal ops."""
    first = copy.deepcopy(prepared["first"])
    for key in ("rotate", "radius", "min_w", "max_w", "min_h", "max_h"):
        first.pop(key, None)
    first.update(type="path", x=geometry["x"], y=geometry["y"], w=geometry["w"], h=geometry["h"], d=geometry["d"])
    return [{"op": "replace", "id": first["id"], "node": first},
            {"op": "remove", "ids": prepared["ids"][1:]}]


def _remove(doc: dict, op: dict, path: str) -> dict:
    ids = op["ids"]
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise _fail("UD-TYPE", join_path(path, "ids"), "expected a non-empty list of ids", ids)
    for n, id_ in enumerate(ids):  # all must exist before any is removed
        _target(doc, id_, join_path(path, "ids", n), ("page", "node", "component"))
    for id_ in ids:
        ref = index_nodes(doc).get(id_)
        if ref is None:  # already gone with an ancestor in this list
            continue
        if ref.kind == "page":
            doc["pages"] = [p for p in doc["pages"] if p is not ref.node]
        elif ref.kind == "component":
            del doc["components"][id_]
        else:
            ref.siblings[:] = [s for s in ref.siblings if s is not ref.node]
    return doc


def _move(doc: dict, op: dict, path: str) -> dict:
    ref = _target(doc, op["id"], join_path(path, "id"), ("node",))
    parent = _target(doc, op["parent"], join_path(path, "parent"), ("page", "node", "component"))
    if op["parent"] in subtree_ids(ref.node):
        raise _fail("UD-OP-CYCLE", join_path(path, "parent"),
                    f"cannot move {op['id']!r} into itself or its descendants", op["parent"])
    kids = _container(parent, join_path(path, "parent"))
    ref.siblings[:] = [s for s in ref.siblings if s is not ref.node]  # index counts after removal
    kids.insert(_index(op.get("index"), len(kids), join_path(path, "index")), ref.node)
    return doc


def _replace(doc: dict, op: dict, path: str) -> dict:
    ref = _target(doc, op["id"], join_path(path, "id"), ("node", "component"))
    node = _node_arg(op["node"], join_path(path, "node"))
    node.setdefault("id", op["id"])
    taken = _taken(doc) - subtree_ids(ref.node)
    assign_ids(node, taken)
    if ref.kind == "component":
        doc["components"][op["id"]] = node
    else:
        ref.siblings[:] = [node if s is ref.node else s for s in ref.siblings]
    return doc


def _set_text(doc: dict, op: dict, path: str) -> dict:
    ref = _target(doc, op["id"], join_path(path, "id"), ("node", "component"))
    if ref.node.get("type") != "text":
        raise _fail("UD-OP-TARGET", join_path(path, "id"),
                    f"{op['id']!r} is a {ref.node.get('type')!r} node, not text", op["id"])
    ref.node["text"] = copy.deepcopy(op["text"])
    return doc


def _deep_merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if v is None:
            dst.pop(k, None)
        elif isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)


def _tokens(doc: dict, op: dict, path: str) -> dict:
    changes = op["set"]
    if not isinstance(changes, dict):
        raise _fail("UD-TYPE", join_path(path, "set"), "expected an object", changes)
    before = {(g, n) for g, v in (doc.get("tokens") or {}).items() if isinstance(v, dict) for n in v}
    tokens = doc.setdefault("tokens", {})
    _deep_merge(tokens, changes)
    after = {(g, n) for g, v in tokens.items() if isinstance(v, dict) for n in v}
    gone = {f"${g}.{n}" for g, n in before - after}
    if gone:
        users = [issue("UD-TOKEN-IN-USE", p, f"{ref} is still used here and was removed", ref)
                 for p, ref in iter_token_refs(doc) if ref in gone]
        if users:
            raise invalid(users)
    return doc


def _component_sets(doc: dict, op: dict, path: str) -> dict:
    """Replace whole definitions; final document validation checks all users."""
    changes = op["set"]
    if not isinstance(changes, dict):
        raise _fail("UD-TYPE", join_path(path, "set"), "expected an object", changes)
    sets = doc.setdefault("component_sets", {})
    for key, definition in changes.items():
        if definition is None:
            sets.pop(key, None)
        elif not isinstance(definition, dict):
            raise _fail("UD-TYPE", join_path(path, "set", key), "expected a definition or null", definition)
        else:
            sets[key] = copy.deepcopy(definition)
    return doc


def _define_component(doc: dict, op: dict, path: str) -> dict:
    cid = op["id"]
    ref = _target(doc, op["from"], join_path(path, "from"), ("node",))
    if not isinstance(cid, str) or cid in _taken(doc):
        raise _fail("UD-DUPLICATE-ID", join_path(path, "id"),
                    f"{cid!r} is not a free id", cid)
    if ref.in_component:
        raise _fail("UD-OP-TARGET", join_path(path, "from"),
                    f"{op['from']!r} is already inside component {ref.in_component!r}", op["from"])
    if ref.node.get("type") == "instance":
        raise _fail("UD-OP-TARGET", join_path(path, "from"),
                    f"{op['from']!r} is already an instance", op["from"])
    root = ref.node
    inst = {"id": root["id"], "type": "instance", "of": cid}
    for k in _PLACEMENT:
        if k in root:
            inst[k] = root[k]
    new_root = copy.deepcopy(root)
    new_root["id"] = cid
    for k in ("x", "y", "absolute", "constraints"):  # placement belongs to each instance
        new_root.pop(k, None)
    doc.setdefault("components", {})[cid] = new_root
    ref.siblings[:] = [inst if s is root else s for s in ref.siblings]
    return doc


def _set_doc(doc: dict, op: dict, path: str) -> dict:
    new = op["doc"]
    if not isinstance(new, dict):
        raise _fail("UD-TYPE", join_path(path, "doc"), "expected a document object", new)
    return normalize_document(new)


_OPS = {"insert": _insert, "update": _update, "remove": _remove, "move": _move,
        "replace": _replace, "set_text": _set_text, "tokens": _tokens,
        "define_component": _define_component, "set_doc": _set_doc,
        "component_sets": _component_sets}
