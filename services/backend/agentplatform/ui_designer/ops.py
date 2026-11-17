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
from agentplatform.ui_designer.schema import (NodeRef, assign_ids, index_nodes, iter_token_refs, lint,
                                              normalize_document, subtree_ids, validate_document)

MAX_OPS = 500
MAX_RESIZE_UNDO_OPS = 128
MAX_RESIZE_UNDO_BYTES = 8 << 10

OP_FIELDS: dict[str, tuple[set[str], set[str]]] = {  # op -> (required, optional)
    "insert": ({"parent", "node"}, {"index"}),
    "update": ({"id", "set"}, set()),
    "resize": ({"id", "set"}, set()),
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
              known_assets: set[str] | None = None) -> ApplyResult:
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
        work = _apply_one(
            work, op, join_path("$.ops", i), captured if resizing else None
        )
    validate_document(work, known_assets=known_assets)
    return ApplyResult(
        doc=work,
        version=version + 1,
        ops=captured.get("forward", copy.deepcopy(ops)),
        warnings=lint(work),
        undo_ops=captured.get("undo"),
    )


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
