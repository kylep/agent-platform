"""Native UI Designer representations of the public @ap/ui components.

Visual states use editable scene nodes. The manifest carries the matching React
props and describes which behaviors and composition features need application code.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from functools import lru_cache
from itertools import product
from pathlib import Path

from .components import nodes
from .schema import MAX_TEXT_BYTES, new_document, validate_document
from .text import layout_text, make_style

CATALOG_ID = "agent-platform"
CATALOG_REVISION = 1
PREFIX = f"ap-ui-v{CATALOG_REVISION}-"


def color(name):
    return f"$color.{PREFIX}{name}"


@lru_cache(maxsize=1)
def _token_snapshot():
    return json.loads(Path(__file__).with_name("platform_tokens.json").read_text())


def platform_tokens():
    """Return shared, mode-keyed foundations under reserved document names."""
    return {
        group: {PREFIX + name: copy.deepcopy(value) for name, value in tokens.items()}
        for group, tokens in _token_snapshot()["tokens"].items()
    }


def _frame(id, w, h, children=(), *, fill=None, border=None, radius=None, **extra):
    frame = {"id": id, "type": "frame", "w": w, "h": h, "children": list(children), **extra}
    if fill:
        frame["fill"] = color(fill)
    if border:
        frame["stroke"] = {"color": color(border), "width": 1, "align": "inside"}
    if radius is not None:
        frame["radius"] = f"$radius.{PREFIX}{radius}" if isinstance(radius, str) else radius
    return frame


def _text(id, text, x, y, w, h, *, ink="text", size=14, weight=400, line=None,
          family="Inter", letter=0, align="left", upper=False):
    style = {"family": family, "size": size, "weight": weight, "line": line or h / size, "letter": letter}
    if style == {"family": "Inter", "size": 14, "weight": 400, "line": 20 / 14, "letter": 0}:
        style = f"$type.{PREFIX}body"
    elif style == {"family": "JetBrains Mono", "size": 13, "weight": 400, "line": 1.625, "letter": 0}:
        style = f"$type.{PREFIX}mono"
    node = {"id": id, "type": "text", "text": text, "x": x, "y": y, "w": w, "h": h,
            "style": style, "color": color(ink), "align": align, "vertical_align": "center"}
    if upper:
        node["text_case"] = "uppercase"
    return node


def _shape(id, x, y, w, h, ink):
    return {"id": id, "type": "rect", "x": x, "y": y, "w": w, "h": h, "fill": color(ink)}


@dataclass(frozen=True)
class Variant:
    id: str
    label: str
    props: dict
    root: dict
    selection: dict
    fixture: dict


def _variant(id, label, root, props=None, *, selection=None, fixture=None):
    return Variant(id, label, props or {}, root, selection or {"state": id}, fixture or {})


@dataclass(frozen=True)
class Component:
    name: str
    subpath: str
    description: str
    variants: tuple
    properties: dict
    limitations: tuple

    @property
    def set_id(self):
        return PREFIX + self.name.lower()

    @property
    def import_path(self):
        return f"@ap/ui/{self.subpath}"


def _component(name, subpath, description, variants, properties=None, limitations=()):
    return Component(name, subpath, description, tuple(variants), properties or {}, tuple(limitations))


def _text_width(label):
    content = label["text"].upper() if label.get("text_case") == "uppercase" else label["text"]
    return layout_text([(content, make_style(label["style"]))]).w


def _inline_width(root):
    layout = root["layout"]
    children = root["children"]
    widths = sum(_text_width(child) if child["type"] == "text" else child["w"] for child in children)
    return round(widths + 2 * layout["pad"][1] + (len(children) - 1) * layout.get("gap", 0), 3)


def _button(text, style="primary", size="md", disabled=False, *, id="button", **extra):
    link = style == "link"
    font_size, line_height, pad_x, pad_y = {
        "md": (14, 20, 12, 6), "sm": (12, 16, 8, 4), "bare": (14, 21, 0, 0)
    }["bare" if link else size]
    if style == "secondary":
        pad_x += 1
        pad_y += 1
    fill = {"primary": "accent", "danger": "danger"}.get(style)
    ink = {"primary": "on-accent", "danger": "on-accent", "link": "link"}.get(style, "text")
    label = _text("children", text, 0, 0, "hug", line_height, ink=ink, size=font_size, weight=500)
    if disabled:
        extra["opacity"] = .5
    layout = {"dir": "row", "pad": [pad_y, pad_x], "align": "center", "justify": "center"}
    return _frame(id, "hug", line_height + 2 * pad_y, [label], fill=fill, radius="md", layout=layout,
                  border="border" if style == "secondary" else None, **extra)


def _buttons():
    variants = []
    for style, size, availability in product(
        ("primary", "secondary", "danger", "link"), ("md", "sm", "bare"), ("enabled", "disabled")
    ):
        default = (style, size, availability) == ("primary", "md", "enabled")
        variant_id = "default" if default else "-".join(
            [style] + ([size] if size != "md" else []) + (["disabled"] if availability == "disabled" else [])
        )
        disabled = availability == "disabled"
        variants.append(_variant(
            variant_id, f"{style.title()} · {size} · {availability}",
            _button("Continue", style, size, disabled),
            {"variant": style, "size": size, "disabled": disabled, "children": "Continue"},
            selection={"style": style, "size": size, "availability": availability},
        ))
    return _component("Button", "button", "Primary, secondary, destructive, and inline actions.",
                      variants, {"children": "Continue"},
                      ["Link appearance always uses the bare size.", "Hover, keyboard focus, and events belong to the app."])


def _banner():
    message = "The deployment is ready."
    variants = []
    for state, ink in (("info", "text"), ("ok", "success"), ("danger", "danger")):
        children = [_text("children", message, 13, 9, 254, 22.75, ink=ink)]
        if state != "info":
            children.insert(0, _frame("outline", 280, 40.75, border=ink, radius="md", opacity=.4))
        variants.append(_variant(
            "default" if state == "info" else state, state.title(),
            _frame("banner", 280, 40.75, children, fill="surface", radius="md",
                   border="border" if state == "info" else None),
            {"variant": state, "children": message},
        ))
    return _component("Banner", "banner", "Inline information, success, and error messages.",
                      variants, {"children": message}, ["The sample uses a fixed available width."])


_CHIP_COLORS = {"neutral": "text-muted", "ok": "success", "warn": "warning", "danger": "danger", "accent": "accent"}


def _chip(text, variant):
    ink = _CHIP_COLORS[variant]
    label = _text("children", text, 0, 0, "hug", 16.5, ink=ink, size=11, weight=500,
                  letter=.55, upper=True)
    layout = {"dir": "row", "pad": [3, 9], "align": "center", "justify": "center"}
    return _frame("chip", "hug", 22.5, [label], border="border" if variant == "neutral" else ink,
                  radius=999, layout=layout)


def _chips():
    label = "Runtime"
    for name in ("Chip", "ChipButton"):
        variants = [_variant("default" if state == "neutral" else state, state.title(),
                             _chip(label, state), {"variant": state, "children": label})
                    for state in _CHIP_COLORS]
        limitations = []
        if name == "ChipButton":
            variants += [
                _variant("pressed", "Pressed filter", _chip(label, "accent"),
                         {"variant": "accent", "aria-pressed": True, "children": label}),
                _variant("disabled", "Disabled", _chip(label, "neutral"),
                         {"variant": "neutral", "disabled": True, "children": label}),
            ]
            limitations = ["Pressed and disabled are static appearances. The app handles toggle and click events."]
        yield _component(name, "chip", "Compact uppercase pill for a label or filter.",
                         variants, {"children": label}, limitations)
    statuses = [("ready", "neutral"), ("succeeded", "ok"), ("running", "warn"), ("failed", "danger")]
    yield _component("StatusChip", "chip", "Status label with semantic color from the platform status vocabulary.", [
        _variant("default" if i == 0 else status, status.title(), _chip(status, state), {"status": status})
        for i, (status, state) in enumerate(statuses)
    ], limitations=["Choose a preset status to keep its appearance and React status value in sync."])


def _field(name):
    captions = {"Input": "Agent name", "Textarea": "Describe this agent.", "CodeEditor": "model: sonnet",
                "Select": "Production"}
    text = captions[name]
    multiline = name in ("Textarea", "CodeEditor")
    height = {"Textarea": 54, "CodeEditor": 56.25, "Select": 33}.get(name, 34)
    width = 232 if multiline else 208
    variants = []
    states = ["default", "disabled"]
    if name != "Select":
        states.append("read-only")
    if name in ("Input", "Select"):
        states.append("invalid")
    for state in states:
        props = {"defaultValue": text}
        if state == "invalid":
            props["aria-invalid"] = True
        if state == "disabled":
            props["disabled"] = True
        if state == "read-only":
            props["readOnly"] = True
        line_height = {"CodeEditor": 21.125, "Select": 19}.get(name, 20)
        label = _text("default-value", text, 11, 7, width - 22, line_height,
                      family="JetBrains Mono" if name == "CodeEditor" else "Inter",
                      size=13 if name == "CodeEditor" else 14)
        children = [label]
        fixture = {}
        if name == "Select":
            children.append({
                "id": "chevron", "type": "path", "x": width - 19, "y": 14, "w": 9, "h": 5,
                "d": "M 0 0 L 4.5 4.5 L 9 0",
                "stroke": {"color": color("text"), "width": 1.4, "cap": "round"},
            })
            fixture["options"] = [text, "Staging"]
        # Select matches CSS :read-only even while enabled. Disabled opacity wins.
        opacity = .5 if state == "disabled" else .7 if state == "read-only" or name == "Select" else 1
        root = _frame("field", width, height, children, fill="surface", border="border", radius="md",
                      opacity=opacity, clip=True)
        variants.append(_variant(state, state.replace("-", " ").title(), root, props, fixture=fixture))
    return _component(
        name, "field", {"Input": "Single-line text field.", "Select": "Native single-choice field.",
                        "Textarea": "Multiline text field.", "CodeEditor": "Monospace multiline field."}[name],
        variants, {} if name == "Select" else {"default-value": text},
        ["Selection and native control behavior belong to the app. Options are a sample composition."]
        if name == "Select" else ["The sample omits native selection, focus, and browser resize controls."],
    )


def _dialog(name):
    title, body, primary = {
        "ConfirmDialog": ("Discard changes?", "Your unsaved changes will be lost.", "Discard"),
        "FormDialog": ("Rename agent", "Choose a name for this agent.", "Save"),
        "InfoDialog": ("About this field", "Agents use this model for their next run.", "Close"),
    }[name]
    label_prop = {"ConfirmDialog": "confirm-label", "FormDialog": "submit-label"}.get(name)
    properties = {"title": title, "children": body}
    if label_prop:
        properties[label_prop] = primary
    variants = []
    for state in ("default", "disabled") if name == "FormDialog" else ("default",):
        disabled = state == "disabled"
        style = {"ConfirmDialog": "danger", "FormDialog": "primary", "InfoDialog": "secondary"}[name]
        action = _button(primary, style, disabled=disabled, id="action")
        action["children"][0]["id"] = label_prop or "close-label"
        # The console base h2 has a 24px top margin. The modal's mb-2
        # utility overrides the base bottom margin only.
        children = [_text("title", title, 21, 45, 406, 28, size=18, weight=600),
                    _text("children", body, 21, 81, 406, 20, ink="text-muted")]
        actions = [action]
        props = {"open": True, "title": title, "children": body}
        if label_prop:
            cancel = _button("Cancel", "secondary", id="cancel")
            cancel["children"][0]["id"] = "cancel-label"
            actions.append(cancel)
            props[_react_prop(label_prop)] = primary
        if name == "FormDialog":
            props["disabled"] = disabled
        children.append(_frame("actions", 406, 34, actions, x=21, y=117, layout={"dir": "row", "gap": f"$space.{PREFIX}s"}))
        root = _frame("dialog", 448, 172, children, fill="raised", border="border", radius="lg")
        variants.append(_variant(state, "Disabled submit" if disabled else "Open", root, props))
    return _component(name, "dialog", title, variants, properties, [
        "The sample represents the open modal panel. The app supplies the overlay, focus management, actions, and any form fields.",
    ])


def _markdown():
    source = "First line.\nSecond line."
    variants = []
    for flow in (False, True):
        display = source.replace("\n", " ") if flow else source
        height = 21.7 if flow else 43.4
        root = _frame("markdown", 260, height, [_text("paragraph", display, 0, 0, 260, height, line=1.55)])
        variants.append(_variant("flow" if flow else "default", "Prose flow" if flow else "Chat line breaks",
                                 root, {"text": source, "flow": flow}))
    return _component("Markdown", "markdown", "Plain paragraph examples in chat and prose modes.", variants,
                      limitations=["The sample lays out native text. Markdown parsing, sanitization, and rich markup require the React component."])


def _quota_bar(id, y, ratio, ink, *, stale=False):
    width = 160
    contents = []
    if ratio is not None:
        label = f"{round(ratio * 100)}%"
        contents.append(_text("track-label", label, 0, 0, width, 12, ink=ink, size=10, line=1,
                              letter=.2, family="JetBrains Mono", align="center"))
        if ratio > 0:
            on_fill = _text("fill-label", label, 0, 0, width, 12, ink="quota-label-on", size=10, line=1,
                            letter=.2, family="JetBrains Mono", align="center")
            contents.append(_frame("fill", width * ratio, 12, [on_fill], fill=ink, clip=True))
        if stale:
            outline = _frame("stale-outline", width, 12, border="text-muted", radius="sm")
            outline["stroke"]["dash"] = [3, 2]
            contents.append(outline)
    return _frame(id, width, 12, contents, y=y, fill="quota-track", radius="sm", clip=True)


def _quota():
    variants = []
    for state in ("default", "stale", "unknown", "two-providers"):
        ratios = (None, None) if state == "unknown" else (.25, .75)
        stale = state == "stale"
        props = {key: {"utilization": ratio, "resets_at": None}
                 for key, ratio in zip(("five_hour", "seven_day"), ratios)}
        props["stale"] = stale
        bars = [_quota_bar("five-hour", 0, ratios[0], "quota-5h", stale=stale),
                _quota_bar("seven-day", 15, ratios[1], "quota-7d", stale=stale)]
        if state == "two-providers":
            props["codex"] = {"five_hour": {"utilization": .4, "resets_at": None},
                              "seven_day": {"utilization": .65, "resets_at": None}}
            bars += [_quota_bar("codex-five-hour", 33, .4, "quota-codex-5h"),
                     _quota_bar("codex-seven-day", 48, .65, "quota-codex-7d")]
        variants.append(_variant(state, {"default": "Current reading", "stale": "Stale reading",
                                         "unknown": "Unknown readings", "two-providers": "Two providers"}[state],
                                 _frame("quota-bars", 160, 60 if state == "two-providers" else 27, bars), props))
    return _component("QuotaBars", "quota", "Sidebar usage meters for Claude and Codex.", variants,
                      limitations=["Percentages and providers are fixed sample readings. Without window data the React component renders no bars."])


def _theme_button(theme, **extra):
    if theme == "dark":
        return _button("☀ light", "secondary", "sm", **extra)
    # Inter has no ◐ glyph. The browser uses a fallback face while the
    # Designer renderer uses editable geometry for the same symbol.
    button = _button("dark", "secondary", "sm", **extra)
    icon = _frame("moon", 10, 10, [
        {"id": "outline", "type": "ellipse", "w": 10, "h": 10,
         "stroke": {"color": color("text"), "width": .8, "align": "inside"}},
        {"id": "shade", "type": "path", "w": 10, "h": 10,
         "d": "M 5 0 A 5 5 0 0 0 5 10 Z", "fill": color("text")},
    ])
    button["children"].insert(0, icon)
    button["layout"]["gap"] = 3
    return button


def _theme_toggle():
    variants = [_variant("default" if theme == "dark" else "light", f"{theme.title()} theme",
                         _theme_button(theme), fixture={"theme": theme})
                for theme in ("dark", "light")]
    return _component("ThemeToggle", "sidenav", "Theme switch button with both label states.", variants,
                      limitations=["The theme hook and persisted preference belong to the app. Choose the label state for the canvas theme."])


def _nav():
    entries = [{"to": "/", "label": "Dashboard", "end": True},
               {"to": "/studio", "label": "Studio", "children": [
                   {"to": "/ui-designer", "label": "UI Designer"},
                   {"to": "/storybook/", "label": "Storybook", "external": True},
               ]}, {"to": "/settings", "label": "Settings"}]
    variants = []
    for state, path in (("default", "/settings"), ("expanded", "/ui-designer")):
        expanded = state == "expanded"
        rows = [("dashboard", "Dashboard", False, False), ("studio", "Studio", False, False)]
        if expanded:
            rows.extend([("designer", "UI Designer", True, True), ("storybook", "Storybook", True, False)])
        rows.append(("settings", "Settings", False, not expanded))
        children = [_text("brand", "Agent Platform", 20, 16, 158, 21, weight=600)]
        y = 57
        for id, title, child, active in rows:
            height = 35.5 if child else 37
            if active:
                children.append(_frame(id + "-selection", 175, height, x=12, y=y, radius="md",
                                       fill="accent", opacity=.12))
            x = 34 if child else 20
            children.append(_text(id, title, x, y + 8, 132, height - 16, size=13 if child else 14,
                                  ink="accent" if active else "text-muted"))
            if id == "studio":
                children.append({
                    "id": "chevron", "type": "path",
                    "x": 170 if expanded else 173, "y": y + (17 if expanded else 16),
                    "w": 8 if expanded else 4, "h": 4 if expanded else 8,
                    "d": "M 0.5 0.5 L 4 3.5 L 7.5 0.5" if expanded else "M 0.5 0.5 L 3.5 4 L 0.5 7.5",
                    "stroke": {"color": color("text-muted"), "width": 1.2, "cap": "round", "join": "round"},
                })
            y += height + 4
        footer = _theme_button("dark", id="footer", x=20, y=y + 8)
        children.append(footer)
        root = _frame("side-nav", 200, y + 58, children, fill="surface")
        root["stroke"] = {"color": color("border"), "sides": [0, 1, 0, 0], "align": "inside"}
        variants.append(_variant(state, "Expanded group" if expanded else "Collapsed group", root,
                                 {"entries": entries, "activePath": path}, fixture={"theme": "dark"}))
    return _component("SideNav", "sidenav", "Sidebar sample with an active link and an expandable group.", variants,
                      limitations=["The sample uses fixed links and a fixed footer. Router integration, group toggles, badges, and custom slots belong to the app."])


def _stat_card(value, label, *, sub=None, warn=False, width=112, id="stat", **position):
    children = [
        _text("value", value, 17, 13, width - 34, 28, size=20, weight=600, ink="warning" if warn else "text"),
        _text("label", label, 17, 43, width - 34, 16.5, size=11, letter=.55, ink="text-subtle", upper=True),
    ]
    if sub:
        children.append(_text("breakdown", sub, 17, 65.5, width - 34, 16.5, size=11, ink="text-muted"))
    return _frame(id, width, 95 if sub else 72.5, children, fill="surface",
                  border="warning" if warn else "border", radius="lg", **position)


def _stat():
    prop = {"label": "Runs", "value": "24"}
    variants = []
    for id, label, props, width in (
        ("default", "Standard", prop, 112),
        ("warning", "Warning", {**prop, "warn": True}, 112),
        ("link", "Linked", {**prop, "to": "/runs"}, 112),
        ("breakdown", "With breakdown", {**prop, "sub": "21 succeeded · 3 failed"}, 256),
    ):
        variants.append(_variant(id, label, _stat_card(
            props["value"], props["label"], warn=props.get("warn", False), sub=props.get("sub"), width=width
        ), props))
    return _component("Stat", "stat", "Metric card with a label, warning color, and optional breakdown.", variants, prop,
                      ["Linked cards show the link appearance. Navigation and rich breakdown content belong to the app."])


def _stat_row():
    cards = [{"value": "24", "label": "Runs"}, {"value": "3", "label": "Failed", "warn": True}]
    standard = [_stat_card(**spec, id=f"card-{i}", x=i * 124) for i, spec in enumerate(cards)]
    wrapping = [_stat_card(**spec, id=f"card-{i}", y=i * 84.5) for i, spec in enumerate(cards)]
    return _component("StatRow", "stat", "A group of metric cards with wrapping at narrow widths.", [
        _variant("default", "Single row", _frame("stat-row", 236, 72.5, standard), fixture={"cards": cards}),
        _variant("wrapping", "Wrapped row", _frame("stat-row", 140, 157, wrapping), fixture={"cards": cards}),
    ], limitations=["The sample presents fixed cards at two available widths. Supply children and container width in React."])


def _cell(id, text, *, header=False, width=128, x=0, y=0):
    height = 12 + 11 * (20 / 14) if header else 32
    label = _text("children", text, 8, 6, width - 16, height - 12, size=11 if header else 14,
                  ink="text-subtle" if header else "text", upper=header, weight=600 if header else 400,
                  letter=.55 if header else 0)
    return _frame(id, width, height, [label], x=x, y=y)


def _table():
    variants = []
    for state in ("default", "empty", "overflowing"):
        overflow = state == "overflowing"
        headers = ["Agent", "Status"] + (["Timestamp"] if overflow else [])
        rows = [["No runs"]] if state == "empty" else [
            ["release", "Succeeded"] + (["10:45 UTC"] if overflow else []),
            ["review", "Running"] + (["11:02 UTC"] if overflow else []),
        ]
        nodes = [_cell(f"header-{i}", header, header=True, x=i*128) for i, header in enumerate(headers)]
        for i, row in enumerate(rows):
            y = nodes[0]["h"] + i * 33
            nodes.append(_shape(f"rule-{i}", 0, y, 128 * len(headers), 1, "border"))
            nodes.extend(_cell(f"row-{i}-{j}", text, width=256 if state == "empty" else 128,
                               x=j * 128, y=y+1) for j, text in enumerate(row))
        root = _frame("table", 256, nodes[0]["h"] + len(rows) * 33, nodes, clip=True)
        variants.append(_variant(state, {"default": "Populated", "empty": "Empty", "overflowing": "Overflowing"}[state],
                                 root, fixture={"headers": headers, "rows": rows}))
    return _component("Table", "table", "Data table sample with styled headers and row dividers.", variants,
                      limitations=["Rows are a fixed sample composition. The overflowing sample shows clipping. Scroll affordances require the React component."])


def _table_cells():
    yield _component("TH", "table", "Uppercase table column heading.", [
        _variant("default", "Header", _cell("header", "Agent", header=True), {"children": "Agent"}),
        _variant("column-scope", "Column scope", _cell("header", "Agent", header=True),
                 {"children": "Agent", "scope": "col"}),
    ], {"children": "Agent"}, ["Use within a table heading row in React."])
    yield _component("TD", "table", "Body cell for a data row.", [
        _variant("default", "Cell", _cell("cell", "release"), {"children": "release"}),
        _variant("spanning-columns", "Two columns", _cell("cell", "release", width=256),
                 {"children": "release", "colSpan": 2}),
    ], {"children": "release"}, ["Use within a table body row in React."])


def _react_prop(name):
    start, *suffix = name.split("-")
    return start + "".join(part.title() for part in suffix)


_CATEGORIES = {
    "banner": "Feedback", "button": "Actions", "chip": "Feedback", "dialog": "Dialogs",
    "field": "Fields", "markdown": "Content", "quota": "Data", "sidenav": "Navigation",
    "stat": "Data", "table": "Data",
}
_STORIES = {
    "Banner": "ui-banner--variants",
    "Button": "ui-button--variants",
    "Chip": "ui-chip--variants",
    "ChipButton": "ui-chip--clickable",
    "StatusChip": "ui-chip--statuses",
    "ConfirmDialog": "ui-confirmdialog--open",
    "FormDialog": "ui-formdialog--rename",
    "InfoDialog": "ui-infodialog--open",
    "CodeEditor": "ui-field--all-fields",
    "Input": "ui-field--all-fields",
    "Select": "ui-field--all-fields",
    "Textarea": "ui-field--all-fields",
    "Markdown": "ui-markdown--chat",
    "QuotaBars": "ui-quotabars--range",
    "SideNav": "ui-sidenav--inside-an-app",
    "ThemeToggle": "ui-sidenav--theme-switch",
    "Stat": "ui-stat--row",
    "StatRow": "ui-stat--wrapping",
    "Table": "ui-table--runs",
    "TD": "ui-table--runs",
    "TH": "ui-table--runs",
}


@lru_cache(maxsize=1)
def _components():
    families = [
        _banner(), _buttons(), *_chips(),
        *(_dialog(name) for name in ("ConfirmDialog", "FormDialog", "InfoDialog")),
        *(_field(name) for name in ("CodeEditor", "Input", "Select", "Textarea")),
        _markdown(), _quota(), _nav(), _theme_toggle(), _stat(), _stat_row(), _table(), *_table_cells(),
    ]
    return {component.name: component for component in families}


def _lookup(name, variant):
    if not isinstance(name, str) or name not in _components():
        raise ValueError(f"Unknown Agent Platform component {name!r}.")
    component = _components()[name]
    for candidate in component.variants:
        if candidate.id == variant:
            return component, candidate
    raise ValueError(f"Unknown {name} variant {variant!r}.")


def catalog_manifest():
    """A copy of the versioned catalog and the props these samples represent."""
    return {
        "id": CATALOG_ID,
        "revision": CATALOG_REVISION,
        "components": [
            {
                "name": component.name,
                "import_path": component.import_path,
                "description": component.description,
                "category": _CATEGORIES[component.subpath],
                "story_id": _STORIES[component.name],
                "properties": [
                    {"name": name, "type": "text", "default": default, "prop": _react_prop(name)}
                    for name, default in component.properties.items()
                ],
                "limitations": list(component.limitations),
                "variants": [
                    {
                        "id": variant.id,
                        "label": variant.label,
                        "props": copy.deepcopy(variant.props),
                        "fixture": copy.deepcopy(variant.fixture),
                        "width": _inline_width(variant.root) if variant.root["w"] == "hug" else variant.root["w"],
                        "height": variant.root["h"],
                        "selection": dict(variant.selection),
                    }
                    for variant in component.variants
                ],
            }
            for component in _components().values()
        ],
    }


catalog = catalog_manifest


@lru_cache(maxsize=64)
def _definitions(name):
    component = _components()[name]
    roots, variants, axes = {}, [], {}
    for variant in component.variants:
        root = copy.deepcopy(variant.root)
        root_id = f"{component.set_id}-{variant.id}"
        root["id"] = root_id
        root["title"] = f"{component.name} · {variant.label}"
        bindings = {name: [] for name in component.properties}
        for i, node in enumerate(nodes(root)):
            if node is root:
                continue
            key = node["id"]
            node["title"] = key.replace("-", " ").capitalize()
            node["id"] = f"{root_id}-{i}"
            if key in bindings:
                bindings[key].append(node["id"])
        roots[root_id] = root
        variants.append({"of": root_id, "when": dict(variant.selection), "bindings": bindings})
        for axis, value in variant.selection.items():
            values = axes.setdefault(axis, [])
            if value not in values:
                values.append(value)
    definition = {
        "axes": axes,
        "properties": {name: {"type": "text", "default": value} for name, value in component.properties.items()},
        "variants": variants,
    }
    return roots, {component.set_id: definition}


def get_definition(name, variant="default"):
    """Return one family's stable ID and its editable definitions."""
    component, _ = _lookup(name, variant)
    roots, sets = _definitions(name)
    return component.set_id, copy.deepcopy(roots), copy.deepcopy(sets)


def make_instance(name, variant="default", *, id, props=None, x=None, y=None):
    """Create a validated selection of native text property overrides."""
    component, selected = _lookup(name, variant)
    if props is not None and not isinstance(props, dict):
        raise ValueError("Expected native text properties as an object.")
    for key, value in (props or {}).items():
        if key not in component.properties:
            raise ValueError(f"Unknown native {name} property {key!r}.")
        if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError(f"{name} property {key!r} must be a bounded string.")
    instance = {
        "id": id, "type": "instance", "of": component.set_id, "title": component.name,
        "variant": dict(selected.selection),
    }
    if props:
        instance["props"] = dict(props)
    if x is not None:
        instance["x"] = x
    if y is not None:
        instance["y"] = y
    return instance


def component_document(name, variant="default"):
    """Build a complete, editable sample document for preview and verification."""
    _, roots, sets = get_definition(name, variant)
    doc = new_document(f"Agent Platform · {name}")
    doc.update(
        modes=["dark", "light"], tokens=platform_tokens(), components=roots, component_sets=sets,
        pages=[{"id": "preview", "title": name, "children": [make_instance(name, variant, id="sample")]}],
    )
    validate_document(doc)
    return doc


def implementation_mapping(instance):
    """Map the selected appearance and approved property bindings to React."""
    if instance.get("type") != "instance":
        return None
    component = next((item for item in _components().values() if item.set_id == instance.get("of")), None)
    if component is None:
        return None
    selection = {**component.variants[0].selection, **instance.get("variant", {})}
    variant = next((item for item in component.variants if item.selection == selection), None)
    if variant is None:
        return None
    props = copy.deepcopy(variant.props)
    values = {**component.properties, **instance.get("props", {})}
    for name in component.properties:
        props[_react_prop(name)] = values[name]
    return {
        "library": CATALOG_ID, "revision": CATALOG_REVISION, "name": component.name,
        "import_path": component.import_path, "variant": variant.id, "props": props,
        "fixture": copy.deepcopy(variant.fixture), "limitations": list(component.limitations),
    }
