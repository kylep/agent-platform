"""The UI Designer cheat sheet served by GET /api/ui-designer/reference.

Every list and number comes from the live constants (schema, ops, the platform
token snapshot, config), so the sheet cannot drift from what the validator accepts.
"""
from __future__ import annotations

import json
from typing import Any

from agentplatform.ui_designer import ops, schema
from agentplatform.ui_designer.platform_catalog import CATALOG_REVISION, PREFIX, platform_tokens


def _j(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


def example_document() -> dict:
    """The document every example below applies to (update/move also need `greeting`)."""
    return {"schema": schema.SCHEMA_VERSION, "title": "Demo", "modes": ["light", "dark"],
            "tokens": {"color": {"ink": "#111111"}}, "pages": [{"id": "home", "children": [
        {"id": "root", "type": "frame", "w": 400, "h": 300,
         "layout": {"dir": "column", "gap": 8, "pad": 16}, "children": []}]}]}


def example_ops() -> dict[str, dict]:
    """One minimal valid op per kind."""
    return {
        "insert": {"op": "insert", "parent": "root", "node": {
            "id": "greeting", "type": "text", "text": "Hello", "color": "$color.ink"}},
        "update": {"op": "update", "id": "greeting", "set": {"text": "Hi", "opacity": 0.8}},
        "move": {"op": "move", "id": "greeting", "parent": "root", "index": 0},
        "tokens": {"op": "tokens", "set": {"color": {"paper": {"light": "#111111", "dark": "#f5f5f5"}}}},
        "platform_component": {"op": "platform_component", "component": "Button", "variant": "default",
                               "revision": CATALOG_REVISION, "parent": "root", "id": "save-btn",
                               "props": {"children": "Save"}},
    }


def build_reference(settings: Any) -> str:
    common = set.intersection(*schema.TYPE_KEYS.values())
    kinds = [f"{t}(+{','.join(sorted(schema.TYPE_KEYS[t] - common))})" if schema.TYPE_KEYS[t] - common
             else t for t in schema.NODE_TYPES]
    short = {g: sorted(n.removeprefix(PREFIX) for n in names) for g, names in platform_tokens().items()}
    op_lines = [f"  {name}: {','.join(sorted(req))}" + (f" [{','.join(sorted(opt))}]" if opt else "")
                for name, (req, opt) in ops.OP_FIELDS.items()]
    ex = example_ops()
    lines = [
        "UI DESIGNER REFERENCE (generated from the live schema)",
        f"Document: {{schema:{schema.SCHEMA_VERSION},title,tokens?,modes?,fonts?,pages:[{{id,title?,children:[node]}}]}}."
        " Ids match [a-z0-9][a-z0-9-]{0,47}; there is no `name` key, use id/title.",
        "Node types: " + " ".join(kinds),
        "Common keys (all types): " + ",".join(sorted(common)),
        f"Layout (frame.layout): {','.join(schema.LAYOUT_KEYS)}. dir={'|'.join(schema.LAYOUT_DIRS)};"
        f" align={'|'.join(schema.LAYOUT_ALIGN)}; justify={'|'.join(schema.LAYOUT_JUSTIFY)};"
        " pad is n, [v,h] or [t,r,b,l]; columns 1-12 (grid only). Children of a laid-out frame take no x/y.",
        f"Sizing (w,h): a number or {'|'.join(schema.SIZE_KEYWORDS)}.",
        f"Fonts: {', '.join(schema.FONT_FAMILIES)} (others must be declared in `fonts`)."
        " type tokens are {family,size,weight?,line?,letter?}.",
        f"Tokens: groups {', '.join(schema.TOKEN_GROUPS)} under doc.tokens, referenced as the whole string"
        " \"$group.name\". A color token is #rgb|#rrggbb|#rrggbbaa or a map {mode:colour}; map keys must be"
        " in doc.modes (1-8 names, e.g. [\"light\",\"dark\"]); a render picks one via `mode` and falls back"
        " to the first mode defined. Removing a token still in use fails.",
        f"Platform tokens (names carry the prefix {PREFIX}, e.g. $color.{PREFIX}{short['color'][0]}):",
        *[f"  {g}: {','.join(v)}" for g, v in short.items()],
        "Load them: a platform_component op merges the platform tokens, the dark+light modes and that"
        " component's definitions into the document. No separate step; browse with list_ui_platform_components.",
        f"Ops (one edit = one atomic batch, max {ops.MAX_OPS}; resize must be alone). required [optional]:",
        *op_lines,
        "Examples (valid as written against a frame `root`; update/move need the inserted node):",
        *[f"  {_j(ex[k])}" for k in ("insert", "update", "move", "tokens", "platform_component")],
        "Edit call: {slug, ops, expected_version, request_id (fresh; reuse only to retry), reason?}.",
        f"Limits: doc {schema.MAX_DOC_BYTES >> 10} KiB, {schema.MAX_NODES} nodes, {schema.MAX_PAGES} pages,"
        f" text {schema.MAX_TEXT_BYTES} B, numbers +-{schema.MAX_NUMBER};"
        f" request body {settings.ui_designer_max_body_bytes >> 10} KiB, text reply"
        f" {settings.ui_designer_text_cap_bytes >> 10} KiB, {settings.ui_designer_max_images_per_request}"
        f" images and {settings.ui_designer_max_megapixels_per_request:g} MP per request;"
        f" agents {settings.ui_designer_writes_per_hour} writes/hour.",
    ]
    return "\n".join(lines)
