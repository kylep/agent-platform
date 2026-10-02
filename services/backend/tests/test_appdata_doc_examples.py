"""The App definitions the app-building skill and the App data reference
teach must be ones the platform accepts (design 39, A13/A14).

Every ```json fence in those two files carries a tag after the language:
- `bundle`: a whole App that validates and self-publishes on a new App;
- `bundle widening`: a whole App that validates but widens a new App's
  approved state, so publishing it needs a proposal;
- `collection`, `view`, `page`: one definition, validated on its own.
An untagged JSON fence fails, so an example can't skip the check.
"""
import json
import re
from pathlib import Path

import pytest

from agentplatform.appdata import authority as A
from agentplatform.appdata.definitions import validate_app, validate_definition

REPO = Path(__file__).resolve().parents[3]
SKILL = REPO / "plugins" / "agent-platform-coding" / "skills" / "app-building" / "SKILL.md"
REFERENCE = REPO / "docs" / "building-blocks" / "app-data.md"
FENCE = re.compile(r"^```json([^\n]*)\n(.*?)^```", re.M | re.S)
TAGS = {"bundle", "bundle widening", "collection", "view", "page"}


def _examples(path: Path) -> list[tuple[str, str, int]]:
    text = path.read_text()
    return [(m.group(1).strip(), m.group(2), text.count("\n", 0, m.start()) + 1)
            for m in FENCE.finditer(text)]


def _cases():
    for path in (SKILL, REFERENCE):
        for tag, body, line in _examples(path):
            yield pytest.param(tag, body, id=f"{path.name}:{line}:{tag or 'untagged'}")


@pytest.mark.parametrize("tag,body", list(_cases()))
def test_every_documented_definition_is_accepted(tag, body):
    assert tag in TAGS, f"tag the fence with one of {sorted(TAGS)}"
    doc = json.loads(body)
    if tag in ("collection", "view", "page"):
        validate_definition(tag, doc)
        return
    validate_app(doc)
    widening = A.widening(A.initial_approved_facts(), A.compute_facts(doc))
    if tag == "bundle":
        assert widening == [], "a `bundle` example must self-publish on a new App"
    else:
        assert widening, "a `bundle widening` example must need a proposal"


def test_the_docs_carry_examples_and_the_skill_fits_a_release():
    skill = _examples(SKILL)
    assert sum(tag == "bundle" for tag, _, _ in skill) >= 3
    assert any(tag == "bundle widening" for tag, _, _ in skill)
    assert any(tag == "bundle" for tag, _, _ in _examples(REFERENCE))
    # plugin_release.MAX_FILE_BYTES: a larger SKILL.md can't be released.
    assert len(SKILL.read_bytes()) <= 64 * 1024
