"""Bounded native component sets, property bindings and pure instance resolution."""

from __future__ import annotations
import copy
import json
from typing import Any, Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from .schema import _Ctx

MAX_SETS = 50
MAX_AXES = 4
MAX_VALUES = 8
MAX_VARIANTS = 64
MAX_PROPERTIES = 32
MAX_TARGETS = 16


def nodes(root: Any) -> Iterator[dict]:
    if isinstance(root, dict):
        yield root
        for child in (
            root.get("children", [])
            if isinstance(root.get("children", []), list)
            else []
        ):
            yield from nodes(child)


def selected_variant(
    doc: dict, instance: dict
) -> tuple[dict | None, dict | None, dict]:
    definition = doc.get("component_sets", {}).get(instance.get("of"))
    if definition is None:
        return None, None, {}
    if not isinstance(definition, dict) or not isinstance(definition.get("axes"), dict):
        raise ValueError("component set axes must be an object")
    if not isinstance(instance.get("variant", {}), dict):
        raise ValueError("variant selection must be an object")
    if any(
        not isinstance(values, list) or not values
        for values in definition["axes"].values()
    ):
        raise ValueError("component set axes require nonempty value lists")
    selection = {
        axis: instance.get("variant", {}).get(axis, values[0])
        for axis, values in definition["axes"].items()
    }
    variant = next(v for v in definition["variants"] if v["when"] == selection)
    return definition, variant, selection


def describe_instance(doc: dict, instance: dict) -> dict:
    definition, variant, selection = selected_variant(doc, instance)
    if definition is None:
        return {}
    values = instance.get("props", {})
    return {
        "component_set": instance["of"],
        "axes": copy.deepcopy(definition["axes"]),
        "variant": selection,
        "properties": {
            name: {
                **copy.deepcopy(spec),
                "value": copy.deepcopy(values.get(name, spec["default"])),
                "bindings": list(variant.get("bindings", {}).get(name, [])),
            }
            for name, spec in definition.get("properties", {}).items()
        },
    }


def resolve_instance(doc: dict, instance: dict) -> tuple[str | None, dict | None]:
    """Selected plain component plus bound props, before instance/legacy overrides.

    Plain instances return their authored component without copying, preserving
    legacy layout caching and byte-identical render behavior.
    """
    definition, variant, _ = selected_variant(doc, instance)
    if definition is None:
        of = instance.get("of")
        return of, doc.get("components", {}).get(of)
    of = variant["of"]
    root = copy.deepcopy(doc["components"][of])
    by_id = {n.get("id"): n for n in nodes(root)}
    for name, spec in definition.get("properties", {}).items():
        value = instance.get("props", {}).get(name, spec["default"])
        field = {"text": "text", "boolean": "visible", "instance-swap": "of"}[
            spec["type"]
        ]
        for target in variant.get("bindings", {}).get(name, []):
            by_id[target][field] = copy.deepcopy(value)
            if spec["type"] == "instance-swap":
                by_id[target].pop("variant", None)
                by_id[target].pop("props", None)
    return of, root


def validate_sets(doc: dict, c: _Ctx) -> None:
    """Closed definitions; schemas use c.add for the standard issue format."""
    from .schema import ID_RE, MAX_TEXT_BYTES

    sets = doc.get("component_sets", {})

    def obj(v, at, allowed, required=()):
        if not isinstance(v, dict):
            c.add("UD-TYPE", at, "expected an object")
            return False
        for k in v.keys() - allowed:
            c.add("UD-UNKNOWN-KEY", at + "." + str(k), "unknown field")
        for k in set(required) - v.keys():
            c.add("UD-MISSING", at + "." + str(k), "required field")
        return True

    if not isinstance(sets, dict):
        c.add(
            "UD-TYPE", "$.component_sets", "expected an object of id -> component set"
        )
        return
    if len(sets) > MAX_SETS:
        c.add("UD-LIMIT", "$.component_sets", f"at most {MAX_SETS} component sets")
        return
    for key, definition in sets.items():
        at = "$.component_sets." + str(key)
        if not isinstance(key, str) or not ID_RE.fullmatch(key):
            c.add("UD-ID", at, "component set keys are node ids")
        elif key in c.ids:
            c.add("UD-ID", at, "component set id is already used")
        else:
            c.ids[key] = at
        if not obj(
            definition, at, {"axes", "properties", "variants"}, ("axes", "variants")
        ):
            continue
        axes, props, variants = (
            definition.get("axes"),
            definition.get("properties", {}),
            definition.get("variants"),
        )
        valid_axes = isinstance(axes, dict) and len(axes) <= MAX_AXES
        if not valid_axes:
            c.add("UD-LIMIT", at + ".axes", f"at most {MAX_AXES} named axes")
            axes = {}
        for name, values in axes.items():
            if not isinstance(name, str) or not ID_RE.fullmatch(name):
                c.add("UD-FORMAT", at + ".axes", "axis names are node ids")
                valid_axes = False
            if not (
                isinstance(values, list)
                and 1 <= len(values) <= MAX_VALUES
                and all(isinstance(v, str) and 1 <= len(v) <= 64 for v in values)
                and len(set(values)) == len(values)
            ):
                c.add(
                    "UD-VALUE",
                    at + ".axes." + str(name),
                    f"1 to {MAX_VALUES} distinct literal values",
                )
                valid_axes = False
        if not isinstance(props, dict) or len(props) > MAX_PROPERTIES:
            c.add(
                "UD-LIMIT", at + ".properties", f"at most {MAX_PROPERTIES} properties"
            )
            props = {}
        if len(json.dumps(props).encode()) > 8192:
            c.add(
                "UD-LIMIT",
                at + ".properties",
                "property definitions total at most 8192 bytes",
            )
        for name, spec in props.items():
            p = at + ".properties." + str(name)
            if not isinstance(name, str) or not ID_RE.fullmatch(name):
                c.add("UD-FORMAT", p, "property names are node ids")
            if not obj(spec, p, {"type", "default", "allowed"}, ("type", "default")):
                continue
            kind, default = spec.get("type"), spec.get("default")
            if kind == "text":
                if (
                    not isinstance(default, str)
                    or len(default.encode()) > MAX_TEXT_BYTES
                ):
                    c.add(
                        "UD-TYPE", p + ".default", "text defaults are bounded strings"
                    )
                if "allowed" in spec:
                    c.add(
                        "UD-UNKNOWN-KEY",
                        p + ".allowed",
                        "allowed is only for instance-swap",
                    )
            elif kind == "boolean":
                if not isinstance(default, bool):
                    c.add("UD-TYPE", p + ".default", "boolean default required")
                if "allowed" in spec:
                    c.add(
                        "UD-UNKNOWN-KEY",
                        p + ".allowed",
                        "allowed is only for instance-swap",
                    )
            elif kind == "instance-swap":
                allowed = spec.get("allowed")
                if not (
                    isinstance(allowed, list)
                    and 1 <= len(allowed) <= MAX_VALUES
                    and all(isinstance(k, str) and k in c.components for k in allowed)
                    and len(set(allowed)) == len(allowed)
                ):
                    c.add(
                        "UD-VALUE",
                        p + ".allowed",
                        "1 to 8 distinct plain component ids",
                    )
                elif not isinstance(default, str) or default not in allowed:
                    c.add("UD-VALUE", p + ".default", "swap default must be allowed")
            else:
                c.add(
                    "UD-VALUE", p + ".type", "text, boolean or instance-swap required"
                )
        if not isinstance(variants, list) or not 1 <= len(variants) <= MAX_VARIANTS:
            c.add(
                "UD-LIMIT", at + ".variants", f"1 to {MAX_VARIANTS} variants required"
            )
            continue
        combinations = set()
        for i, variant in enumerate(variants):
            p = at + f".variants[{i}]"
            if not obj(variant, p, {"when", "of", "bindings"}, ("when", "of")):
                continue
            when = variant.get("when")
            if not (
                valid_axes
                and isinstance(when, dict)
                and when.keys() == axes.keys()
                and all(isinstance(v, str) and v in axes[k] for k, v in when.items())
            ):
                c.add("UD-VALUE", p + ".when", "one declared value for every axis")
            else:
                combo = tuple(sorted(when.items()))
                if combo in combinations:
                    c.add("UD-VALUE", p + ".when", "duplicate variant combination")
                combinations.add(combo)
            of = variant.get("of")
            if not isinstance(of, str) or of not in c.components:
                c.add(
                    "UD-COMPONENT-UNKNOWN",
                    p + ".of",
                    "variant must name a plain component",
                )
                continue
            targets = {
                n.get("id"): n
                for n in nodes(c.components[of])
                if isinstance(n.get("id"), str) and n.get("id") != of
            }
            bindings = variant.get("bindings", {})
            if not isinstance(bindings, dict):
                c.add("UD-TYPE", p + ".bindings", "expected property -> descendant ids")
                continue
            for name in props.keys() - bindings.keys():
                c.add(
                    "UD-MISSING",
                    p + ".bindings." + str(name),
                    "every property needs a binding in every variant",
                )
            used = set()
            for name, ids in bindings.items():
                bp = p + ".bindings." + str(name)
                spec = props.get(name)
                if not isinstance(spec, dict):
                    c.add("UD-VALUE", bp, "unknown property")
                    continue
                if not (
                    isinstance(ids, list)
                    and 1 <= len(ids) <= MAX_TARGETS
                    and all(isinstance(x, str) for x in ids)
                    and len(set(ids)) == len(ids)
                ):
                    c.add("UD-LIMIT", bp, f"1 to {MAX_TARGETS} distinct descendant ids")
                    continue
                for target in ids:
                    node = targets.get(target)
                    field = (
                        {
                            "text": "text",
                            "boolean": "visible",
                            "instance-swap": "of",
                        }.get(spec.get("type"))
                        if isinstance(spec.get("type"), str)
                        else None
                    )
                    binding = (target, field)
                    if binding in used:
                        c.add(
                            "UD-VALUE",
                            bp,
                            "properties cannot bind the same target field",
                        )
                    used.add(binding)
                    if (
                        node is None
                        or (spec.get("type") == "text" and node.get("type") != "text")
                        or (
                            spec.get("type") == "instance-swap"
                            and node.get("type") != "instance"
                        )
                    ):
                        c.add(
                            "UD-VALUE",
                            bp,
                            "binding target missing or incompatible node type",
                        )
                    if (
                        node is not None
                        and spec.get("type") == "instance-swap"
                        and isinstance(spec.get("allowed"), list)
                    ):
                        from .schema import _check_overrides

                        for replacement in spec["allowed"]:
                            if (
                                isinstance(replacement, str)
                                and replacement in c.components
                            ):
                                _check_overrides(
                                    c, bp, node, replacement, c.components[replacement]
                                )
        if valid_axes:
            total = 1
            for values in axes.values():
                total *= len(values)
            if total > MAX_VARIANTS or len(combinations) != total:
                c.add(
                    "UD-VALUE",
                    at + ".variants",
                    f"exactly one variant per combination, at most {MAX_VARIANTS}",
                )


def validate_instance(doc: dict, instance: dict, at: str, c: _Ctx) -> None:
    from .schema import MAX_TEXT_BYTES

    sets = doc.get("component_sets", {})
    definition = (
        sets.get(instance.get("of"))
        if isinstance(sets, dict) and isinstance(instance.get("of"), str)
        else None
    )
    if not isinstance(definition, dict):
        for key in ("variant", "props"):
            if key in instance:
                c.add(
                    "UD-VALUE",
                    at + "." + key,
                    "only component set instances have this field",
                )
        return
    axes, props = definition.get("axes", {}), definition.get("properties", {})
    for key, allowed in [("variant", axes), ("props", props)]:
        values = instance.get(key, {})
        if not isinstance(values, dict):
            c.add("UD-TYPE", at + "." + key, "expected an object")
            continue
        if key == "props" and len(json.dumps(values).encode()) > 8192:
            c.add("UD-LIMIT", at + ".props", "property values total at most 8192 bytes")
        if not isinstance(allowed, dict):
            continue
        for name, value in values.items():
            p = at + "." + key + "." + str(name)
            if name not in allowed:
                c.add("UD-VALUE", p, "unknown selection or property")
                continue
            if key == "variant":
                if (
                    not isinstance(value, str)
                    or not isinstance(allowed[name], list)
                    or value not in allowed[name]
                ):
                    c.add("UD-VALUE", p, "unknown axis value")
            else:
                spec = allowed[name]
                if not isinstance(spec, dict):
                    continue
                kind = spec.get("type")
                valid = (
                    (
                        kind == "text"
                        and isinstance(value, str)
                        and len(value.encode()) <= MAX_TEXT_BYTES
                    )
                    or (kind == "boolean" and isinstance(value, bool))
                    or (
                        kind == "instance-swap"
                        and isinstance(value, str)
                        and isinstance(spec.get("allowed"), list)
                        and value in spec["allowed"]
                    )
                )
                if not valid:
                    c.add(
                        "UD-TYPE",
                        p,
                        "property value does not match its declared type or allowed values",
                    )


def conservative_expansion(doc: dict) -> int:
    """Memoized worst-case graph costs, never a Cartesian product of props.

    Every variant and allowed swap is checked, including unused definitions and
    invisible children. Per-metric maxima may overestimate mixed alternatives.
    """
    from .schema import (
        MAX_INSTANCE_DEPTH,
        MAX_EXPANDED_NODES,
        MAX_SHADOWS,
        MAX_BLURS,
        MAX_BACKDROPS,
        MAX_STEPPED,
        effect_counts,
    )
    from .errors import UiDesignerError

    comps, sets = doc.get("components", {}), doc.get("component_sets", {})
    cache, stack = {}, set()

    def maximum(options):
        return tuple(max(v[i] for v in options) for i in range(6))

    def add(a, b):
        return tuple(a[i] + b[i] for i in range(5)) + (max(a[5], b[5]),)

    def definition(key):
        if key in cache:
            return cache[key]
        if key in stack:
            raise UiDesignerError(
                "UD-EXPANSION",
                "component reference cycle",
                detail={"at": key, "reason": "cycle"},
            )
        if len(stack) > MAX_INSTANCE_DEPTH:
            raise UiDesignerError(
                "UD-EXPANSION",
                "component nesting exceeds its depth budget",
                detail={"at": key, "reason": "depth"},
            )
        stack.add(key)
        if key in sets:
            options = []
            spec = sets[key]
            for variant in spec["variants"]:
                swaps = {}
                for prop, targets in variant.get("bindings", {}).items():
                    p = spec.get("properties", {}).get(prop, {})
                    if p.get("type") == "instance-swap":
                        for target in targets:
                            swaps[target] = p["allowed"]
                options.append(node(comps[variant["of"]], swaps))
            result = maximum(options)
        else:
            result = node(comps[key], {})
        stack.remove(key)
        cache[key] = result
        return result

    def node(n, swaps):
        if n.get("type") == "instance":
            candidates = swaps.get(n.get("id"), [n["of"]])
            cost = maximum([definition(key) for key in candidates])
            # Shared/legacy overrides can add effects; conservatively retain base too.
            extra = list(effect_counts(n))
            from .schema import paints_of

            for key, value in n.get("overrides", {}).items():
                prop = key.rsplit(".", 1)[-1]
                if prop == "shadow":
                    extra[0] += (
                        len(value) if isinstance(value, list) else 1 if value else 0
                    )
                elif prop == "blur" and value:
                    extra[1] += 1
                elif prop == "backdrop_blur" and value:
                    extra[2] += 1
                elif prop == "fill":
                    extra[3] += sum(
                        isinstance(v, dict)
                        and v.get("gradient") in ("angular", "diamond")
                        for v in paints_of(value)
                    )
            return (cost[0], *(cost[i + 1] + extra[i] for i in range(4)), cost[5] + 1)
        cost = (1, *effect_counts(n), 0)
        for child in n.get("children", []):
            cost = add(cost, node(child, swaps))
        return cost

    limits = (
        MAX_EXPANDED_NODES,
        MAX_SHADOWS,
        MAX_BLURS,
        MAX_BACKDROPS,
        MAX_STEPPED,
        MAX_INSTANCE_DEPTH,
    )

    def check(cost, at):
        if any(a > b for a, b in zip(cost, limits)):
            raise UiDesignerError(
                "UD-EXPANSION",
                "component expansion exceeds its work budget",
                detail={"at": at, "cost": cost, "limits": limits},
            )

    for key in [*comps, *sets]:
        check(definition(key), key)
    total = (0, 0, 0, 0, 0, 0)
    for page in doc.get("pages", []):
        for child in page.get("children", []):
            total = add(total, node(child, {}))
    check(total, "pages")
    return total[0]
