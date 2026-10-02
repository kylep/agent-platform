"""A bounded App tool-view action; reads only via the executor's call proxy.

The proxy injects `_app_data.url` after stripping anything the model sent by
that name. It holds the credential, so this subprocess receives no bearer.
"""
import json
import re
import sys
from collections import Counter
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

FIELD = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
PAGE_SIZE = 1000
MAX_SCANNED = 1_000_000


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError("the App data endpoint redirected")


def _endpoint(raw: str) -> str:
    parsed = urlsplit(raw)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not parsed.path.endswith("/api/app-data")):
        raise ValueError("invalid App data endpoint")
    return raw.rstrip("/") + "/scan"


def _scan(url: str, field: str, cursor: str | None) -> dict:
    body = json.dumps({"role": "source", "fields": [field], "limit": PAGE_SIZE,
                       "cursor": cursor}).encode()
    request = Request(url, data=body, headers={"Content-Type": "application/json"},
                      method="POST")
    with build_opener(_NoRedirect()).open(request, timeout=25) as response:
        if response.status != 200:
            raise ValueError(f"App data scan returned {response.status}")
        raw = response.read(262_145)
        if len(raw) > 262_144:
            raise ValueError("App data scan page is too large")
    value = json.loads(raw)
    if not isinstance(value, dict) or not isinstance(value.get("rows"), list):
        raise TypeError("invalid App data scan response")
    return value


def counts(args: dict) -> dict:
    if args.get("action") != "counts":
        raise ValueError("unknown action")
    field = args.get("field")
    if not isinstance(field, str) or not FIELD.fullmatch(field):
        raise ValueError("field must be an App field name")
    top = args.get("top", 10)
    if type(top) is not int or not 1 <= top <= 20:
        raise ValueError("top must be 1–20")
    endpoint = _endpoint((args.get("_app_data") or {}).get("url", ""))
    tally: Counter[str | None] = Counter()
    cursor = None
    scanned = 0
    while True:
        batch = _scan(endpoint, field, cursor)
        rows = batch["rows"]
        if len(rows) > PAGE_SIZE or scanned + len(rows) > MAX_SCANNED:
            raise ValueError("App data scan exceeded its row limit")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("values"), dict):
                raise TypeError("invalid App data scan row")
            value = row["values"].get(field)
            if value is not None and (not isinstance(value, (str, int, float, bool))
                                      or len(str(value)) > 128):
                raise ValueError("field value is not a short scalar")
            tally[None if value is None else str(value)] += 1
        scanned += len(rows)
        next_cursor = batch.get("next_cursor")
        if next_cursor is None:
            break
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
            raise ValueError("invalid App data scan cursor")
        cursor = next_cursor
    ranked = sorted(tally.items(), key=lambda item: (-item[1], item[0] is None,
                                                     item[0] or ""))[:top]
    return {"rows": [{"value": value, "count": count} for value, count in ranked]}


def main() -> int:
    try:
        print(json.dumps(counts(json.load(sys.stdin))))
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
