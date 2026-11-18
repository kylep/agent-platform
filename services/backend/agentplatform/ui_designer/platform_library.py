"""Install and inspect revisioned Agent Platform components as native document data."""
from __future__ import annotations

import copy
import json
from functools import cache

from . import platform_catalog as catalog
from .components import MAX_PROPERTIES
from .errors import invalid, issue, join_path
from .schema import ID_RE, index_nodes, subtree_ids

_PLATFORM_MODES = ("dark", "light")
_INSTANCE_PLACEMENT = {"id", "type", "of", "title", "x", "y", "absolute", "variant", "props", "constraints"}


@cache
def _components_by_id() -> dict[str, dict]:
    return {catalog.get_definition(component["name"])[0]: component
            for component in catalog.catalog_manifest()["components"]}


def _merge_entries(destination: dict, entries: dict, path: str, *, component_roots: bool = False) -> None:
    for key, value in entries.items():
        if key not in destination:
            destination[key] = copy.deepcopy(value)
        elif destination[key] == value:
            continue
        elif component_roots and destination[key] == catalog.legacy_component_definition(value):
            # Normalize the first release's ignored coordinates without changing
            # its appearance or requiring users to detach pinned instances.
            destination[key] = copy.deepcopy(value)
        else:
            raise invalid([issue("UD-VALUE", join_path(path, key),
                                 "Conflicts with the pinned Agent Platform component library")])


def install_instance(doc: dict, op: dict, path: str) -> dict:
    """Install only this component's definitions and the shared token revision."""
    revision = op["revision"]
    if type(revision) is not int or revision != catalog.CATALOG_REVISION:
        raise invalid([issue("UD-VALUE", join_path(path, "revision"),
                             f"Expected Agent Platform library revision {catalog.CATALOG_REVISION}")])
    name, variant, node_id = op["component"], op["variant"], op["id"]
    for key, value in (("component", name), ("variant", variant)):
        if not isinstance(value, str) or not 1 <= len(value) <= 80:
            raise invalid([issue("UD-FORMAT", join_path(path, key), "Expected a catalog identifier")])
    if not isinstance(node_id, str) or not ID_RE.fullmatch(node_id):
        raise invalid([issue("UD-ID", join_path(path, "id"), "Expected a Designer node id")])
    props = op.get("props", {})
    if not isinstance(props, dict):
        raise invalid([issue("UD-TYPE", join_path(path, "props"), "Expected a property object")])
    if len(props) > MAX_PROPERTIES or not all(isinstance(key, str) and ID_RE.fullmatch(key) for key in props):
        raise invalid([issue("UD-FORMAT", join_path(path, "props"), "Expected bounded native property names")])
    try:
        _, roots, sets = catalog.get_definition(name, variant)
        instance = catalog.make_instance(name, variant, id=node_id, props=props,
                                         x=op.get("x"), y=op.get("y"))
    except (KeyError, ValueError) as error:
        raise invalid([issue("UD-VALUE", path, str(error))]) from error

    live_modes = doc.get("modes", [])
    modes = [*live_modes, *(mode for mode in _PLATFORM_MODES if mode not in live_modes)]
    if len(modes) > 8:
        raise invalid([issue("UD-MODE", "$.modes", "Agent Platform colors require dark and light modes")])
    doc["modes"] = modes
    tokens = doc.setdefault("tokens", {})
    for group, entries in catalog.platform_tokens().items():
        _merge_entries(tokens.setdefault(group, {}), entries, join_path("$.tokens", group))

    nodes = index_nodes(doc)
    live_sets = doc.get("component_sets") or {}
    for key, definition in roots.items():
        for node_key in sorted(subtree_ids(definition)):
            if node_key in live_sets or (node_key in nodes and nodes[node_key].in_component != key):
                raise invalid([issue("UD-DUPLICATE-ID", join_path("$.components", key),
                                     f"Agent Platform definition id {node_key!r} is already in use")])
    for key in sets:
        if key in nodes:
            raise invalid([issue("UD-DUPLICATE-ID", join_path("$.component_sets", key),
                                 f"Agent Platform component set id {key!r} is already in use")])
    _merge_entries(doc.setdefault("components", {}), roots, "$.components", component_roots=True)
    _merge_entries(doc.setdefault("component_sets", {}), sets, "$.component_sets")
    return instance


def _matches_entries(destination: dict, approved: dict, *, component_roots: bool = False) -> bool:
    return all(destination.get(key) == value or (
        component_roots and destination.get(key) == catalog.legacy_component_definition(value)
    ) for key, value in approved.items())


def inspect_instance(doc: dict, instance: dict) -> dict | None:
    component_id = instance.get("of", "")
    if instance.get("type") != "instance" or not component_id.startswith(catalog.PREFIX):
        return None
    component = _components_by_id().get(component_id)
    if component is None:
        return None
    name = component["name"]
    _, roots, sets = catalog.get_definition(name)
    tokens = doc.get("tokens") or {}
    source_matches = (
        _matches_entries(doc.get("components") or {}, roots, component_roots=True)
        and _matches_entries(doc.get("component_sets") or {}, sets)
        and all(_matches_entries(tokens.get(group, {}), entries)
                for group, entries in catalog.platform_tokens().items())
    )
    mapping = catalog.implementation_mapping(instance)
    canonical = catalog.make_instance(name, mapping["variant"], id=instance["id"]) if mapping else {}
    visual_fields = {key: value for key, value in instance.items()
                     if key not in _INSTANCE_PLACEMENT and (key != "overrides" or value)}
    canonical_fields = {key: value for key, value in canonical.items() if key not in _INSTANCE_PLACEMENT}
    exact = source_matches and mapping is not None and visual_fields == canonical_fields
    props = mapping["props"] if exact else {}
    result = {"status": "exact" if exact else "detached", "component": name,
              "import_path": component["import_path"], "revision": catalog.CATALOG_REVISION,
              "variant": mapping["variant"] if mapping else None, "props": props,
              "snippet": None, "fixture": mapping["fixture"] if exact else {},
              "limitations": component["limitations"]}
    if component.get("story_id"):
        result["story_id"] = component["story_id"]
    if exact:
        result["snippet"] = _snippet(component, mapping)
    return result


def _jsx_value(value) -> str:
    return "{" + json.dumps(value, ensure_ascii=True).replace("<", "\\u003c") + "}"


def _jsx_attributes(props: dict) -> str:
    return "".join(f" {key}={_jsx_value(value)}" for key, value in props.items())


def _snippet(component: dict, mapping: dict) -> str:
    name, path = component["name"], component["import_path"]
    props, fixture = mapping["props"], mapping["fixture"]
    imports = [name]
    attributes = _jsx_attributes(props)
    content = f"<{name}{attributes} />"
    signature = ""
    note = ""
    if name == "Select":
        options = "\n".join(f"    <option value={_jsx_value(option)}>{_jsx_value(option)}</option>"
                            for option in fixture["options"])
        content = f"<Select{attributes}>\n{options}\n  </Select>"
    elif name == "StatRow":
        imports.append("Stat")
        cards = "\n".join(f"      <Stat{_jsx_attributes(card)} />" for card in fixture["cards"])
        variant = next(variant for variant in component["variants"] if variant["id"] == mapping["variant"])
        width = _jsx_value({"width": variant["width"]})
        content = f"<div style={width}>\n    <StatRow>\n{cards}\n    </StatRow>\n  </div>"
    elif name == "Table":
        imports.extend(("TH", "TD"))
        headers, rows = fixture["headers"], fixture["rows"]
        heading = "".join(f'<TH scope="col">{_jsx_value(header)}</TH>' for header in headers)
        body = []
        for row in rows:
            cells = "".join(f"<TD{_jsx_attributes({'colSpan': len(headers)} if len(row) == 1 else {})}>"
                            f"{_jsx_value(value)}</TD>" for value in row)
            body.append(f"        <tr>{cells}</tr>")
        style = _jsx_value({"minWidth": len(headers) * 128, "tableLayout": "fixed"})
        content = "\n".join((
            '<div style={{ width: 256 }}>', f"    <Table style={style}>",
            f"      <thead><tr>{heading}</tr></thead>", "      <tbody>", *body,
            "      </tbody>", "    </Table>", "  </div>",
        ))
    elif name in ("TH", "TD"):
        imports.append("Table")
        section = "thead" if name == "TH" else "tbody"
        content = f"<Table>\n      <{section}><tr>{content}</tr></{section}>\n    </Table>"
    elif name == "SideNav":
        imports.append("ThemeToggle")
        content = f"<SideNav{attributes} footer={{<ThemeToggle />}} />"
    elif name in ("ConfirmDialog", "FormDialog", "InfoDialog"):
        callbacks = {"ConfirmDialog": ("onConfirm", "onCancel"),
                     "FormDialog": ("onSubmit", "onCancel"), "InfoDialog": ("onClose",)}[name]
        declarations = ", ".join(f"{callback}: () => void" for callback in callbacks)
        signature = f"{{ {', '.join(callbacks)} }}: {{ {declarations} }}"
        handlers = "".join(f" {callback}={{{callback}}}" for callback in callbacks)
        content = f"<{name}{attributes}{handlers} />"
    if "theme" in fixture:
        note = f'// Set the app theme to {fixture["theme"]} to match this sample.\n'

    return (f'import {{ {", ".join(imports)} }} from "{path}";\n\n{note}'
            f"export function Example({signature}) {{\n  return (\n    {content}\n  );\n}}")
