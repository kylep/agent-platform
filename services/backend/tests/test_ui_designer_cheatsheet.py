"""The ui_designer cheat sheet (agentspec TOOL_HELP and the broker docstring) must stay
true: its example ops are applied through the real ops and schema."""
import ast
import json
from pathlib import Path

from agentplatform.agentspec import TOOL_HELP, TOOL_UI_DESIGNER, UI_DESIGNER_CHEATSHEET
from agentplatform.ui_designer.ops import OP_FIELDS, apply_ops
from agentplatform.ui_designer.schema import NODE_TYPES, TYPE_KEYS

BROKER = Path(__file__).resolve().parents[2] / "mcp-broker" / "broker.py"


def example_ops():
    line = UI_DESIGNER_CHEATSHEET.split("Example edit ops")[1].splitlines()[1].strip()
    return json.loads(line)


def test_example_ops_apply_and_validate():
    empty = {"schema": 1, "pages": []}
    res = apply_ops(empty, example_ops(), version=1, expected_version=1)
    page = res.doc["pages"][0]
    root = page["children"][0]
    assert [c["type"] for c in root["children"]] == ["text", "frame"]
    assert len(root["children"][1]["children"]) >= 2


def test_sheet_names_every_op_and_node_type():
    for op in OP_FIELDS:
        assert op in UI_DESIGNER_CHEATSHEET
    for t in NODE_TYPES:
        assert t in UI_DESIGNER_CHEATSHEET
    for k in TYPE_KEYS["frame"] | TYPE_KEYS["text"]:
        assert k in UI_DESIGNER_CHEATSHEET, k
    assert len(UI_DESIGNER_CHEATSHEET.splitlines()) <= 60


def test_tool_help_and_broker_docstring_carry_the_same_sheet():
    entry = next(t for t in TOOL_HELP if t["name"] == TOOL_UI_DESIGNER)
    assert UI_DESIGNER_CHEATSHEET in entry["description"]
    tree = ast.parse(BROKER.read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "ui_designer")
    assert UI_DESIGNER_CHEATSHEET in ast.get_docstring(fn, clean=False).replace("\n    ", "\n") or \
        UI_DESIGNER_CHEATSHEET in ast.get_docstring(fn, clean=False)
