"""Tool tests with a fake Discord API; never send a live message."""
import io
import json
import pytest
import jsonschema
import yaml
from pathlib import Path

import run
from run import chunks


def test_chunks_respect_limit_and_lines():
    text = "\n".join(f"line {i} " + "x" * 100 for i in range(40))
    parts = chunks(text, size=500)
    assert all(len(p) <= 500 for p in parts)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")


def test_chunks_hard_split_long_line():
    parts = chunks("y" * 4200, size=1900)
    assert [len(p) for p in parts] == [1900, 1900, 400]


def test_chunks_short_text_is_one_part():
    assert chunks("hello") == ["hello"]


def test_unique_name_and_exact_id(monkeypatch):
    def fake_req(path, _token, payload=None):
        assert payload is None
        return {
            "/users/@me/guilds": [{"id": "one"}, {"id": "two"}],
            "/guilds/one/channels": [{"id": "123456789012345678", "type": 0,
                                      "name": "general"}],
            "/guilds/two/channels": [{"id": "223456789012345678", "type": 0,
                                      "name": "alerts"}],
            "/channels/223456789012345678": {"id": "223456789012345678",
                                              "type": 0, "name": "alerts"},
        }[path]
    monkeypatch.setattr(run, "_req", fake_req)
    assert run.find_channel("token", "general")["id"] == "123456789012345678"
    assert run.channel_by_id("token", "223456789012345678")["name"] == "alerts"


def test_ambiguous_name_is_rejected(monkeypatch):
    def fake_req(path, _token, payload=None):
        return {
            "/users/@me/guilds": [{"id": "one"}, {"id": "two"}],
            "/guilds/one/channels": [{"id": "123456789012345678", "type": 0,
                                      "name": "general"}],
            "/guilds/two/channels": [{"id": "223456789012345678", "type": 0,
                                      "name": "general"}],
        }[path]
    monkeypatch.setattr(run, "_req", fake_req)
    with pytest.raises(ValueError, match="multiple servers"):
        run.find_channel("token", "general")
    with pytest.raises(ValueError, match="Discord channel ID"):
        run.channel_by_id("token", "general")


def test_manifest_requires_one_exact_destination():
    params = yaml.safe_load((Path(__file__).parent / "tool.yaml").read_text())["params"]
    jsonschema.validate({"channel": "alerts", "text": "hello"}, params)
    jsonschema.validate({"channel_id": "223456789012345678", "text": "hello"}, params)
    jsonschema.validate({"identity_id": "discord-second",
                         "channel_id": "223456789012345678", "text": "hello"}, params)
    for args in ({"text": "hello"},
                 {"identity_id": "", "channel_id": "223456789012345678",
                  "text": "hello"},
                 {"channel": "alerts", "channel_id": "223456789012345678",
                  "text": "hello"},
                 {"channel_id": "not-an-id", "text": "hello"}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(args, params)


def test_direct_executor_refuses_another_identity(monkeypatch, capsys):
    monkeypatch.setattr(run.sys, "stdin", io.StringIO(json.dumps({
        "identity_id": "discord-second", "channel_id": "223456789012345678",
        "text": "hello"})))
    monkeypatch.setenv("token", "default-bot-token")
    monkeypatch.setattr(run, "_req", lambda *a, **k: pytest.fail("default bot was used"))
    assert run.main() == 2
    assert "platform API" in capsys.readouterr().err
