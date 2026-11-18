"""Stable error codes for UI Designer documents and ops (design 46).

Agents act on these codes, so a code never changes meaning once shipped. A
rejected write raises one `UiDesignerError`: `UD-INVALID` carries every issue
found (`{path, code, message, fix}`), the others carry a `detail` object.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agentplatform.appdata.errors import join_path, json_path  # noqa: F401  (re-exported)

# Top-level codes an `edit` call answers with.
UD_INVALID = "UD-INVALID"
UD_EXPANSION = "UD-EXPANSION"
UD_VERSION_CONFLICT = "UD-VERSION-CONFLICT"
UD_ASSET_UNKNOWN = "UD-ASSET-UNKNOWN"
UD_RENDER_FAILED = "UD-RENDER-FAILED"
UD_IMAGE_TOO_LARGE = "UD-IMAGE-TOO-LARGE"
UD_OP_TARGET = "UD-OP-TARGET"
UD_NOT_FOUND = "UD-NOT-FOUND"
UD_SLUG_TAKEN = "UD-SLUG-TAKEN"
UD_REQUEST_REUSED = "UD-REQUEST-REUSED"
UD_BUDGET_STORAGE = "UD-BUDGET-STORAGE"
UD_BUDGET_WRITES = "UD-BUDGET-WRITES"
UD_BUDGET_PIXELS = "UD-BUDGET-PIXELS"
UD_NO_IMAGES_HERE = "UD-NO-IMAGES-HERE"
UD_FORBIDDEN = "UD-FORBIDDEN"
UD_BODY_TOO_LARGE = "UD-BODY-TOO-LARGE"

# code -> the fix suggested when a check has nothing more specific to say.
CODES: dict[str, str] = {
    UD_INVALID: "Fix every listed issue and resubmit the whole batch.",
    UD_EXPANSION: "Flatten the instance nesting (depth <= 6) or split the document; "
                  "a component may not contain itself.",
    UD_VERSION_CONFLICT: "Apply your change on top of the ops listed since your "
                         "expected_version, then retry with the current version.",
    UD_ASSET_UNKNOWN: "Place the image into this document with `place` first.",
    UD_RENDER_FAILED: "Retry once; if it repeats, snapshot a smaller node or simplify the document.",
    UD_IMAGE_TOO_LARGE: "Snapshot a smaller region (`nodes=[...]`) or a lower scale.",
    UD_NOT_FOUND: "Check the slug or version number; `history` lists what exists.",
    UD_SLUG_TAKEN: "Pick another slug, or delete the existing document first.",
    UD_REQUEST_REUSED: "Use a fresh request_id for a different call.",
    UD_BUDGET_STORAGE: "Reclaim space: hard-delete a document, prune old versions, "
                       "or run asset GC (Kyle's session).",
    UD_BUDGET_WRITES: "Wait `retry_after` seconds; batch more ops into one `edit`.",
    UD_BUDGET_PIXELS: "Wait `retry_after` seconds; snapshot fewer or smaller nodes, or use "
                      "`snapshot as=boxes`.",
    UD_NO_IMAGES_HERE: "Images go to agent runs; use the web UI, or call without `snapshot`.",
    UD_BODY_TOO_LARGE: "Send a smaller request body: fewer ops per call, or split the document.",
    UD_FORBIDDEN: "This action is for the document's owner or an administrator.",
    # Issues inside UD-INVALID.
    "UD-UNKNOWN-KEY": "Remove the key; the schema is closed.",
    "UD-MISSING": "Add the required key.",
    "UD-TYPE": "Use the JSON type the schema declares for this key.",
    "UD-VALUE": "Use one of the values the schema allows here.",
    "UD-BOUNDS": "Keep the value inside the bounds the schema declares.",
    "UD-FORMAT": "Write the value in the format the schema describes.",
    "UD-LIMIT": "Stay inside the document limits (size, nodes, pages, components).",
    "UD-ID": "Use lowercase letters, digits and dashes, starting with a letter or "
             "digit, at most 48 characters.",
    "UD-DUPLICATE-ID": "Give every node, page and component a distinct id.",
    "UD-TOKEN-UNKNOWN": "Define the token under `tokens`, or use a literal.",
    "UD-TOKEN-KIND": "Use a token from the group this property accepts.",
    "UD-TOKEN-IN-USE": "Change the listed users first, then remove the token.",
    "UD-COMPONENT-UNKNOWN": "Point `of` at a key of `components`.",
    "UD-OVERRIDE": "Override `<child-id>.<prop>` (or `<prop>` for the root) of a "
                   "property the target node accepts.",
    "UD-FONT": "Use Inter, JetBrains Mono or Noto Sans.",
    "UD-MODE": "Key mode-dependent tokens by the names listed in `modes`.",
    # Op problems.
    "UD-OP-UNKNOWN": "Use one of: insert, update, resize, boolean, icon, platform_component, remove, move, replace, set_text, "
                     "tokens, define_component, set_doc.",
    "UD-OP-TARGET": "Name an id that exists and is allowed for this op.",
    "UD-OP-INDEX": "Use an index between 0 and the parent's child count.",
    "UD-OP-CYCLE": "A node cannot move into itself or its own descendants.",
    "UD-OP-FIELD": "This op does not accept that field.",
}


@dataclass(frozen=True)
class Issue:
    code: str
    path: str
    message: str
    value: Any = None
    fix: str = ""

    def as_dict(self) -> dict:
        out = {"path": self.path, "code": self.code, "message": self.message,
               "fix": self.fix}
        if self.value is not None:
            out["value"] = _jsonable(self.value)
        return out


def issue(code: str, path: str, message: str, value: Any = None,
          fix: str | None = None) -> Issue:
    if code not in CODES:
        raise KeyError(f"unregistered UI designer error code {code}")
    return Issue(code=code, path=path, message=message, value=value,
                 fix=fix if fix is not None else CODES[code])


class UiDesignerError(ValueError):
    """A refused write. `code` is a top-level code; `issues` only for UD-INVALID."""

    def __init__(self, code: str, message: str = "", *, issues: list[Issue] | None = None,
                 detail: dict | None = None):
        if code not in CODES:
            raise KeyError(f"unregistered UI designer error code {code}")
        self.code = code
        self.issues = list(issues or [])
        self.detail = dict(detail or {})
        if not message and self.issues:
            message = "; ".join(f"{i.code} at {i.path}: {i.message}" for i in self.issues)
        self.message = message or CODES[code]
        super().__init__(f"{code}: {self.message}")

    @property
    def codes(self) -> list[str]:
        return [i.code for i in self.issues]

    def as_dict(self) -> dict:
        out: dict = {"ok": False, "code": self.code, "message": self.message,
                     "fix": CODES[self.code]}
        if self.issues:
            out["errors"] = [i.as_dict() for i in self.issues]
        out.update(self.detail)
        return out


def invalid(issues: list[Issue]) -> UiDesignerError:
    return UiDesignerError(UD_INVALID, issues=issues)


def _jsonable(value: Any) -> Any:
    try:
        text = json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)[:200]
    return value if len(text) <= 500 else text[:500] + "…"
