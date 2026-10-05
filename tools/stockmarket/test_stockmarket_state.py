"""The App-data view envelope keeps system fields inside values."""

import run


def test_latest_brief_reads_view_envelope(monkeypatch):
    monkeypatch.setattr(run, "_base", lambda _args: "proxy/")
    monkeypatch.setattr(run, "_call", lambda _root, action, **_body: {
        "rows": [{"id": "brief-1", "values": {
            "version": 3, "day": "2026-10-04", "body": "A calm close",
            "tags_json": "[]", "indexes_json": "[]", "movers_json": "[]"}}],
        "next_cursor": None,
    })
    assert run.run({"action": "latest"}) == {"brief": {
        "day": "2026-10-04", "body": "A calm close", "tags": [],
        "indexes": [], "movers": []}}
