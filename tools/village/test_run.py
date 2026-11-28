"""village tool client: request shape, headers, error mapping, no secrets read."""
import io
import json
import urllib.error

import pytest

import run

ENV = {"TOOL_CALLER_AGENT": "village-planner", "TOOL_RUN_ID": "a" * 32}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(status=200, body=None, sink=None):
    def opener(req, timeout):
        if sink is not None:
            sink.append((req, timeout))
        payload = json.dumps(body if body is not None else {"ok": True, "data": {"x": 1}}).encode()
        if status != 200:
            raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(payload))
        return _Resp(payload)
    return opener


def test_request_shape_and_verified_headers():
    req = run.build_request({"action": "say", "request_id": "r1", "line": "hi",
                             "_app_data": {"url": "x"}}, ENV)
    assert req.full_url == run.ENGINE_URL and req.get_method() == "POST"
    assert req.get_header("X-caller-agent") == "village-planner"
    assert req.get_header("X-run-id") == "a" * 32
    assert json.loads(req.data) == {"action": "say", "args": {"request_id": "r1", "line": "hi"}}


def test_no_secret_is_read_or_sent():
    env = {**ENV, "VILLAGE_SECRET": "s3cret", "AP_INTERNAL_SECRET": "s3cret"}
    req = run.build_request({"action": "look"}, env)
    assert "s3cret" not in json.dumps(dict(req.header_items())) + req.data.decode()
    assert {h.lower() for h, _ in req.header_items()} == {"content-type", "x-caller-agent", "x-run-id"}
    source = open(run.__file__).read()
    assert "SECRET" not in source and "TOOL_DB_URL" not in source


def test_missing_identity_or_action_is_refused():
    with pytest.raises(run.VillageError):
        run.build_request({"action": "look"}, {})
    with pytest.raises(run.VillageError):
        run.build_request({}, ENV)


def test_output_is_wrapped_as_data():
    sink = []
    out = io.StringIO()
    import contextlib
    with contextlib.redirect_stdout(out):
        rc = run.main(io.StringIO('{"action": "look"}'), ENV, _opener(sink=sink))
    assert rc == 0
    text = out.getvalue()
    assert text.startswith("<village-data") and text.rstrip().endswith("</village-data>")
    assert '{"x": 1}' in text
    assert sink[0][1] == run.TIMEOUT_S and run.TIMEOUT_S < 45


@pytest.mark.parametrize("status,word", [(403, "refused"), (409, "different arguments"),
                                         (422, "invalid"), (503, "unavailable")])
def test_error_mapping(status, word, capsys):
    reason = {403: "this run has finished", 409: "different arguments", 422: "invalid",
              503: "not running"}[status]
    rc = run.main(io.StringIO('{"action": "look"}'), ENV,
                  _opener(status, {"ok": False, "reason": reason}))
    assert rc == 2
    err = capsys.readouterr().err
    assert word in err and reason in err


def test_engine_down_is_a_clean_error(capsys):
    def opener(req, timeout):
        raise urllib.error.URLError("connection refused")
    assert run.main(io.StringIO('{"action": "look"}'), ENV, opener) == 2
    assert "village unavailable" in capsys.readouterr().err
