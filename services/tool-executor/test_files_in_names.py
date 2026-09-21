"""The two ends of `files_in` agree on a name (docs/design/25 "Broker").

The broker turns an artifact's name into the filename a tool reads it by
(`broker._plain_filename`); the executor refuses a name that is not a plain
filename (`executor._safe_name`, inside `stage_files_in`). They are written
apart, so this is the one place the rule is held in both hands: whatever the
broker makes of a hostile name, the executor must stage — or the broker
would turn an artifact a caller may read into a 400 the model cannot act on.
The broker module is loaded the way its own tests load it (fastmcp stood
in for), by path, so this runs with the executor's requirements alone.

    cd services/tool-executor && ../backend/.venv/bin/python -m pytest -q test_files_in_names.py
"""
import base64
import sys
from pathlib import Path

import pytest

import executor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp-broker"))
from test_relay_tool import broker

ID = "ab" * 16
HOSTILE = ["..", ".", "", None, "a\x00b", "a/b", "a\\b", "../../etc/passwd", "x" * 300,
           "é" * 100, "é" * 101, "résumé.xml", "  junit.xml  ", "/", "\\", ".hidden",
           "dir/", "a/../b", "sp ace.json"]


@pytest.mark.parametrize("name", HOSTILE)
def test_what_the_broker_makes_of_a_name_the_executor_stages(name, tmp_path):
    safe = broker._plain_filename(name, ID)
    assert executor._safe_name(safe), (name, safe)
    executor.stage_files_in(tmp_path, [executor.FileIn(
        name=safe, mime="", b64=base64.b64encode(b"hi").decode())])
    assert [p.name for p in tmp_path.iterdir()] == [safe]
    assert (tmp_path / safe).read_bytes() == b"hi"


@pytest.mark.parametrize("name", ["junit.xml", "playwright.json", "coverage.xml",
                                  "résumé.xml", "sp ace.json", ".hidden"])
def test_a_plain_name_passes_through_unchanged(name):
    assert broker._plain_filename(name, ID) == name
    assert executor._safe_name(name)


@pytest.mark.parametrize("name", ["", None, ".", "..", "a\x00b", "x" * 300, "é" * 101, "/"])
def test_a_name_the_executor_would_refuse_becomes_the_artifact_id(name):
    """The executor's rule, seen from the broker's side: what it would refuse
    is what the broker replaces — no name is ever a reason to fail the call."""
    assert broker._plain_filename(name, ID) == ID


def test_the_caps_are_the_executors_own():
    assert broker.FILES_MAX == executor.FILES_IN_MAX
    assert broker.FILE_BYTES_MAX == executor.FILE_CAP
    assert broker._NAME_MAX == executor.NAME_MAX
