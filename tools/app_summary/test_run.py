"""The reference view tool reads only the per-call App scan endpoint."""
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import jsonschema
import pytest
import yaml

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("app_summary_run", HERE / "run.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class Scan(BaseHTTPRequestHandler):
    calls: ClassVar[list] = []

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.calls.append((self.path, body, dict(self.headers)))
        rows = ([{"values": {"status": "open"}},
                 {"values": {"status": "closed"}}] if body["cursor"] is None else
                [{"values": {"status": "open"}}, {"values": {"status": None}}])
        answer = json.dumps({"rows": rows,
                             "next_cursor": "next" if body["cursor"] is None else None}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(answer)))
        self.end_headers()
        self.wfile.write(answer)


def test_counts_from_only_the_injected_scan_endpoint():
    Scan.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Scan)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = tool.counts({"action": "counts", "field": "status", "top": 2,
            "_app_data": {"url": f"http://127.0.0.1:{server.server_port}/api/app-data"}})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert result == {"rows": [{"value": "open", "count": 2},
                              {"value": "closed", "count": 1}]}
    assert [path for path, _, _ in Scan.calls] == ["/api/app-data/scan"] * 2
    assert all(body["role"] == "source" and body["fields"] == ["status"]
               and body["limit"] == 1000 for _, body, _ in Scan.calls)
    assert all("authorization" not in {k.lower() for k in headers}
               for _, _, headers in Scan.calls)
    manifest = yaml.safe_load((HERE / "tool.yaml").read_text())
    jsonschema.validate(result, manifest["view_actions"]["counts"]["output_schema"])


@pytest.mark.parametrize("url", ["https://example.com/api/app-data",
    "http://127.0.0.1:8000/other", "http://localhost:8000/api/app-data",
    "http://127.0.0.1:8000/api/app-data?x=1"])
def test_no_other_endpoint(url):
    with pytest.raises(ValueError, match="invalid App data endpoint"):
        tool.counts({"action": "counts", "field": "status", "_app_data": {"url": url}})


def test_invalid_field_and_limit_are_rejected_before_scan():
    for args in ({"field": "secret.path", "top": 1}, {"field": "status", "top": 21}):
        with pytest.raises(ValueError):
            tool.counts({"action": "counts", "_app_data": {
                "url": "http://127.0.0.1:8000/api/app-data"}, **args})
