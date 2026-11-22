"""Pure, bounded authored geometry pins, shared by resize ops and instances.

Only free-positioned or absolute children participate. Intrinsic/fill geometry
cannot supply an authored baseline; active pins on a changed unknown axis are
refused rather than silently computed from guessed renderer pixels.
"""
from __future__ import annotations

import copy
import math
from typing import Any

from .errors import invalid, issue

MAX_NUMBER = 100_000
PIN_VALUES = ("start", "end", "center", "stretch", "scale")


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _clamp(value: float, node: dict, axis: str) -> float:
    return max(float(node.get("min_" + axis, 0)),
               min(float(node.get("max_" + axis, MAX_NUMBER)), value))


def effective_size(node: dict) -> tuple[float | None, float | None]:
    """Numeric dimensions after the same authored min/max clamp as layout."""
    result = []
    for axis in ("w", "h"):
        number = _number(node.get(axis))
        result.append(None if number is None else _clamp(number, node, axis))
    return tuple(result)


def _eligible(parent: dict, child: dict, index: int) -> bool:
    return not parent.get("layout") or child.get("absolute") is True or (index == 0 and bool(parent.get("mask")))


def has_pins(parent: dict, axis: str | None = None) -> bool:
    """Cheap direct-child check so legacy instance expansion needs no copies."""
    axes = (axis,) if axis else ("horizontal", "vertical")
    return any(_eligible(parent, child, index) and any(
        child.get("constraints", {}).get(name, "start") != "start" for name in axes)
        for index, child in enumerate(parent.get("children", [])))


def _fail(node: dict, message: str) -> None:
    raise invalid([issue("UD-VALUE", "$.constraints", message, node.get("id"))])


def _coordinate(value: float, node: dict) -> float:
    if not math.isfinite(value) or abs(value) > MAX_NUMBER:
        _fail(node, "resized pin geometry exceeds the coordinate budget")
    return round(value, 6)


def resize_children(parent: dict, old_size: tuple, new_size: tuple) -> dict:
    """Return a copy whose eligible children follow pins, recursively.

    The caller keeps the parent's authored requested dimensions. Clamps affect
    the deltas and child sizes; exact undo must restore literal old geometry.
    """
    result = copy.deepcopy(parent)

    def resize(container: dict, before: tuple, after: tuple) -> None:
        for index, child in enumerate(container.get("children", [])):
            if not _eligible(container, child, index):
                continue
            original_size = effective_size(child)
            for dimension, (axis, position, extent) in enumerate((
                ("horizontal", "x", "w"), ("vertical", "y", "h"))):
                pin = child.get("constraints", {}).get(axis, "start")
                if pin == "start" or before[dimension] == after[dimension]:
                    continue
                old, new = before[dimension], after[dimension]
                if old is None or new is None:
                    _fail(child, "active pins require fixed numeric parent dimensions on the resized axis")
                coordinate = _number(child.get(position, 0))
                extent_value = original_size[dimension]
                if coordinate is None or extent_value is None:
                    _fail(child, "active pins require fixed numeric child position and size on the resized axis")
                delta = new - old
                if pin == "end":
                    coordinate += delta
                elif pin == "center":
                    coordinate += delta / 2
                elif pin == "stretch":
                    extent_value = _clamp(extent_value + delta, child, extent)
                elif pin == "scale":
                    if old <= 0:
                        _fail(child, "scale pins require a positive original parent dimension")
                    coordinate *= new / old
                    extent_value = _clamp(extent_value * new / old, child, extent)
                coordinate = _coordinate(coordinate, child)
                if coordinate != child.get(position, 0):
                    child[position] = coordinate
                if pin in ("stretch", "scale") and extent_value != child.get(extent):
                    child[extent] = _coordinate(extent_value, child)
            changed_size = effective_size(child)
            if child.get("type") == "frame" and changed_size != original_size:
                resize(child, original_size, changed_size)
    resize(result, old_size, new_size)
    return result


def prepare_instance_resize(component: dict, merged: dict, resolver) -> dict:
    """The renderer's copied-component pin adapter, shared with write preflight.

    Resolving geometry here affects only the expansion copy. Authored resize
    operations deliberately keep their existing numeric-only contract.
    """
    def head(value):
        clone = dict(value)
        for field in ('x','y','w','h','min_w','max_w','min_h','max_h'):
            if isinstance(clone.get(field),str) and clone[field].startswith('$'):
                clone[field] = resolver(clone[field])
        return clone
    def geometry(value):
        clone=head(value)
        if 'children' in clone:
            clone['children']=[geometry(child) for child in clone['children']]
        return clone
    old_root=new_root=None
    pending=[component]
    while pending:
        container=pending.pop()
        if has_pins(container):
            if old_root is None:
                old_root,new_root=geometry(component),head(merged)
            before,after=effective_size(old_root),effective_size(new_root)
            baseline=effective_size(head(container))
            for i,(axis,field) in enumerate((('horizontal','w'),('vertical','h'))):
                if not has_pins(container,axis): continue
                raw_changed=component.get(field)!=merged.get(field)
                if baseline[i] is None and (before[i]!=after[i] or raw_changed):
                    _fail(container,'pinned instance resizing needs fixed numeric container sizes on the changing axis')
                if before[i] is None and after[i] is None and raw_changed:
                    _fail(container,'pinned instance resizing needs fixed numeric sizes on the changing axis')
        pending.extend(container.get('children',[]))
    if old_root is not None and has_pins(old_root):
        return resize_children(old_root,effective_size(old_root),effective_size(new_root))
    return component


def validate_instance_resize(doc: dict, instance: dict) -> None:
    """Validate exactly the renderer's non-mutating expansion in every mode.

    Building expands nested components/sets, applies root overrides before
    pins and descendant overrides afterwards, and enforces the same depth and
    node budgets. No shaping, layout measurement, painting or asset I/O occurs.
    """
    from .layout import _Builder
    for mode in doc.get('modes') or [None]:
        builder=_Builder(doc,mode,measure_text=False)
        builder.build(instance,'',None,0,None,0,'$.instance')
