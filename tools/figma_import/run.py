"""Bounded, internal Figma REST fetcher. Credentials never leave the process.

API contract checked against developers.figma.com/docs/rest-api/file-endpoints/
and /rate-limits/ (2026-11-16). Redirects are always refused, including CDN
redirects: an allowlisted starting host must never authorize another host.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CAP = 8 * 1024 * 1024
API = "https://api.figma.com/v1"
# Exact known Figma asset origins, never arbitrary *.amazonaws.com hosts.
CDN_HOSTS = {"s3-alpha.figma.com", "s3-alpha-sig.figma.com", "figma-alpha-api.s3.us-west-2.amazonaws.com"}
KEY = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
NODE = re.compile(r"^[A-Za-z0-9:_-]{1,128}$")


class FetchError(Exception):
    def __init__(self, code, **details):
        self.result = {"ok": False, "error": code, **details}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise FetchError("UD-IMPORT-REDIRECT-REFUSED")


def download(url, *, token=None, cap=CAP):
    # urlsplit silently strips some control characters; reject before parsing.
    if (not isinstance(url, str) or len(url) > 16384 or
            any(ord(c) <= 32 or ord(c) == 127 for c in url)):
        raise FetchError("UD-IMPORT-URL-REFUSED")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        raise FetchError("UD-IMPORT-URL-REFUSED") from None
    hosts = {"api.figma.com"} if token is not None else CDN_HOSTS
    if (parsed.scheme != "https" or parsed.hostname not in hosts or
            parsed.username or parsed.password or port not in (None, 443)):
        raise FetchError("UD-IMPORT-URL-REFUSED")
    headers = {"User-Agent": "agent-platform-figma-import", "Accept-Encoding": "identity"}
    if token is not None:
        headers["X-Figma-Token"] = token
    try:
        with urllib.request.build_opener(NoRedirect()).open(
                urllib.request.Request(url, headers=headers), timeout=20) as response:
            body = response.read(cap + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            try:
                retry = max(1, int(exc.headers.get("Retry-After", "60")))
            except ValueError:
                retry = 60
            raise FetchError("UD-IMPORT-RATE-LIMITED", retry_after_seconds=retry) from None
        raise FetchError("UD-IMPORT-UPSTREAM", status=exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise FetchError("UD-IMPORT-UNREACHABLE") from None
    if len(body) > cap:
        raise FetchError("UD-IMPORT-FILE-TOO-BIG")
    return body


def api(path, params, token):
    body = download(API + path + "?" + urllib.parse.urlencode(params), token=token)
    try:
        data = json.loads(body)
        if not isinstance(data, dict) or data.get("error") or data.get("err"):
            raise FetchError("UD-IMPORT-BAD-RESPONSE")
        return data
    except (ValueError, UnicodeError):
        raise FetchError("UD-IMPORT-BAD-RESPONSE") from None


def ids(args, key):
    values = args.get(key, [])
    if (not isinstance(values, list) or not 1 <= len(values) <= 8 or
            any(not isinstance(v, str) or not NODE.fullmatch(v) for v in values)):
        raise FetchError("UD-IMPORT-BAD-ARGS", field=key)
    return values


def write_file(out, name, body, **meta):
    if len(body) > CAP:
        raise FetchError("UD-IMPORT-FILE-TOO-BIG")
    (out / name).write_bytes(body)
    if meta:
        (out / (name + ".meta.json")).write_text(json.dumps(meta))


def run(args):
    key = args.get("file_key", "")
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise FetchError("UD-IMPORT-BAD-ARGS", field="file_key")
    token = os.environ.get("FIGMA_TOKEN", "").strip()
    if not token:
        raise FetchError("UD-IMPORT-NOT-CONFIGURED")
    out = Path(os.environ["TOOL_OUT_DIR"])
    mode = args.get("mode")
    if not mode:
        mode = {"tree": "tree_page" if args.get("page_ids") or args.get("node_ids") else "file_meta",
                "images": "asset_batch", "references": "references"}.get(args.get("what"))
    params = {}
    if args.get("version"):
        if not isinstance(args["version"], str) or not KEY.fullmatch(args["version"]):
            raise FetchError("UD-IMPORT-BAD-ARGS", field="version")
        params["version"] = args["version"]
    if mode in ("file_meta", "tree_page"):
        if mode == "file_meta":
            params["depth"] = 1
        else:
            params.update(ids=",".join(ids(args, "node_ids" if args.get("node_ids") else "page_ids")), geometry="paths")
        try:
            data = api(f"/files/{key}", params, token)
        except FetchError as exc:
            if exc.result["error"] == "UD-IMPORT-FILE-TOO-BIG":
                # A shallow retry finds frame ids without buffering the oversized tree.
                params["depth"] = 2
                shallow = api(f"/files/{key}", params, token)
                pages = shallow.get("document", {}).get("children", [])
                if args.get("page_ids"):
                    pages = [p for p in pages if p.get("id") in args["page_ids"]]
                frames = [{"id": n["id"], "name": str(n.get("name", ""))[:64]}
                          for p in pages for n in p.get("children", [])]
                if len(frames) > 256:
                    raise FetchError("UD-IMPORT-TOO-MANY-FRAMES")
                raise FetchError("UD-IMPORT-PAGE-TOO-BIG", frames=frames) from None
            raise
        if not isinstance(data.get("document"), dict):
            raise FetchError("UD-IMPORT-BAD-RESPONSE")
        body = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
        write_file(out, "tree.json", body)
        result = {"ok": True, "tree_file": "tree.json", "version": data.get("version")}
        if mode == "file_meta":
            pages = data.get("document", {}).get("children", [])
            if len(pages) > 256:
                raise FetchError("UD-IMPORT-TOO-MANY-PAGES")
            result.update(name=str(data.get("name", ""))[:128], pages=[{"id": p["id"], "name": str(p.get("name", ""))[:64]} for p in pages])
        return result
    if mode == "asset_batch":
        wanted = ids(args, "image_refs")
        data = api(f"/files/{key}/images", {}, token)
        # OpenAPI specifies meta.images; endpoint prose also shows images at
        # top level. Accept either documented envelope, never a silent empty map.
        mapping = data.get("meta", {}).get("images") if isinstance(data.get("meta"), dict) else None
        if mapping is None:
            mapping = data.get("images")
        label = "image_ref"
    elif mode == "references":
        wanted = ids(args, "node_ids")
        params.update(ids=",".join(wanted), format="png", scale=1)
        mapping = api(f"/images/{key}", params, token).get("images")
        label = "node_id"
    else:
        raise FetchError("UD-IMPORT-BAD-ARGS", field="mode")
    if not isinstance(mapping, dict):
        raise FetchError("UD-IMPORT-BAD-RESPONSE")
    # Fetch every file before publishing any sink files; a retry has no partial result.
    fetched = []
    missing = []
    for ref in wanted:
        url = mapping.get(ref)
        if not url:
            missing.append(ref)
            continue
        if not isinstance(url, str):
            raise FetchError("UD-IMPORT-BAD-RESPONSE")
        fetched.append((ref, download(url)))
    files = []
    for index, (ref, body) in enumerate(fetched):
        name = f"{'reference' if mode == 'references' else 'asset'}-{index}.bin"
        write_file(out, name, body, **{label: ref})
        files.append({"name": name, label: ref})
    return {"ok": True, "files": files, "missing": missing}


def main():
    try:
        result = run(json.load(sys.stdin))
    except FetchError as exc:
        result = exc.result
    except Exception:
        # Never stringify upstream exceptions: request URLs can contain signed secrets.
        result = {"ok": False, "error": "UD-IMPORT-FETCH-FAILED"}
    output = json.dumps(result, ensure_ascii=False)
    if len(output.encode()) > 128 * 1024:
        output = json.dumps({"ok": False, "error": "UD-IMPORT-METADATA-TOO-BIG"})
    print(output)


if __name__ == "__main__":
    main()
