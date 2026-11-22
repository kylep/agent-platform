"""Shared documents for the UI Designer layout, font and benchmark tests."""
import copy


def big_doc(rows: int = 90, cards_per_row: int = 8, toolbar_nodes: int = 28, toolbars: int = 140) -> dict:
    """A representative large document: instances of nested components (a card
    holds four list-item instances) in wrapped rows, plus authored toolbars of
    plain text and shapes. Defaults give ~4,900 authored and ~19,990 expanded nodes."""
    item = {"type": "frame", "layout": {"dir": "row", "gap": 4, "pad": [2, 6], "align": "center"},
            "fill": "$color.surface", "radius": 4, "children": [
                {"id": "dot", "type": "ellipse", "w": 8, "h": 8, "fill": "$color.accent"},
                {"id": "name", "type": "text", "text": "Player one", "style": "$type.body", "color": "$color.fg"},
                {"id": "score", "type": "text", "text": "1200", "style": "$type.hud", "color": "$color.fg"},
                {"id": "bar", "type": "rect", "w": 24, "h": 4, "fill": "$color.accent"}]}
    card = {"type": "frame", "w": 200, "layout": {"dir": "column", "gap": 4, "pad": 8, "align": "stretch"},
            "fill": "$color.panel", "radius": 8, "children": [
                {"id": "title", "type": "text", "text": "Team standings for the round", "style": "$type.body",
                 "color": "$color.fg", "w": "fill"},
                {"id": "r1", "type": "instance", "of": "list-item"},
                {"id": "r2", "type": "instance", "of": "list-item", "overrides": {"name.text": "Second"}},
                {"id": "r3", "type": "instance", "of": "list-item", "overrides": {"score.text": "980"}},
                {"id": "r4", "type": "instance", "of": "list-item"}]}
    toolbar_kids = []
    for i in range(toolbar_nodes):
        if i % 2:
            toolbar_kids.append({"id": f"tb{{n}}-{i}", "type": "text", "text": f"Tab {i}", "style": "$type.body",
                                 "color": "$color.fg"})
        else:
            toolbar_kids.append({"id": f"tb{{n}}-{i}", "type": "rect", "w": 12, "h": 12, "fill": "$color.accent"})
    pages_children = []
    for r in range(rows):
        pages_children.append({"id": f"row{r}", "type": "frame", "layout": {"dir": "row", "gap": 8, "wrap": True},
                               "w": 1900, "children": [
                                   {"id": f"c{r}-{c}", "type": "instance", "of": "card"}
                                   for c in range(cards_per_row)]})
    for n in range(toolbars):
        tb = copy.deepcopy(toolbar_kids)
        for k in tb:
            k["id"] = k["id"].format(n=n)
        pages_children.append({"id": f"tb{n}", "type": "frame", "w": 1900, "h": 24,
                               "layout": {"dir": "row", "gap": 6, "align": "center"}, "children": tb})
    return {
        "schema": 1, "title": "Benchmark", "modes": ["dark", "light"],
        "tokens": {
            "color": {"bg": {"dark": "#0b0f14", "light": "#ffffff"}, "fg": {"dark": "#e6edf3", "light": "#111111"},
                      "accent": "#5ad1ff", "panel": {"dark": "#161b22", "light": "#f0f2f5"},
                      "surface": {"dark": "#21262d", "light": "#e3e6ea"}},
            "type": {"body": {"family": "Inter", "size": 14, "weight": 400, "line": 1.4},
                     "hud": {"family": "JetBrains Mono", "size": 14, "weight": 600}}},
        "components": {"list-item": item, "card": card},
        "pages": [{"id": "p", "title": "Page", "children": [
            {"id": "root", "type": "frame", "w": 1920, "layout": {"dir": "column", "gap": 4, "pad": 8},
             "fill": "$color.bg", "children": pages_children}]}]}
