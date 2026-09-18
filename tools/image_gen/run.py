"""image_gen tool: text-to-image (and reference-guided edits) via OpenAI,
Google Gemini, or Black Forest Labs FLUX — stdlib only (urllib).

Ported from claude-ttrpg/tools/imagegen.py into the platform's tool contract
(docs/design/23): JSON args on stdin, one JSON summary line on stdout, the
image itself in the file sink (`TOOL_OUT_DIR/image.<ext>`) with a
`image.<ext>.meta.json` sidecar the executor folds into the file's `meta`.
The CLI, spend ledger, env-file loading and Imagen are gone: the API does the
budgeting (this tool only reports the registry's `cost_usd` estimate), the
executor delivers the keys, and Imagen is gated behind legacy accounts.

`internal: true` — only the platform API calls this, never an agent. Keys
exist only in this subprocess's env for the duration of one call and are
never printed; error text carries at most 300 chars of a provider body.

Request shapes and model ids were verified 2026-09-17 against:
  OpenAI  developers.openai.com/api/docs/api-reference/images/{create,createEdit}
  Gemini  ai.google.dev/gemini-api/docs/generate-content/image-generation
          (generateContent is now "legacy" but fully supported; it is the shape
          proven with Kyle's key. Google's newer Interactions API is not used.)
  BFL     api.bfl.ai/openapi.json, docs.bfl.ml/flux_2/*, kontext/*, pricing
Model ids drift; models.json is the registry and the place to fix them.

Geometry bridging (each provider wants a different shape):
  - Entries with `sizes` take a "WxH" `size`. OpenAI's gpt-image-2+ and every
    BFL width/height model accept custom sizes (`custom_size: true`); OpenAI
    wants multiples of 16 inside its documented bounds, BFL gets snapped to
    the 16-pixel grid. gpt-image-1-mini is limited to its listed sizes. An
    `aspect` given to a sizes-model is bridged to ~1 MP pixels on the 16-grid
    (or, without custom sizes, to the nearest listed size).
  - Entries with `aspects` (Gemini, FLUX.1 Kontext) take an "a:b" `aspect`;
    a `size` given to one is bridged to the nearest listed aspect.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MODELS_PATH = Path(__file__).with_name("models.json")

# provider -> (env var the executor injects, the secret block that carries it)
KEY_ENV = {
    "openai": ("OPENAI_API_KEY", "openai-api-key"),
    "gemini": ("GEMINI_API_KEY", "gemini-api-key"),
    "bfl": ("BFL_API_KEY", "bfl-api-key"),
}

# OpenAI has no "default" tier; "high" is what the ttrpg tool always used.
DEFAULT_QUALITY = {"openai": "high"}

# One wall-clock budget for the whole call, under the 180 s manifest timeout
# with room for the error mapping to run: past the manifest timeout the
# executor SIGKILLs the process and the user sees "tool timed out" instead
# of the provider's reason. Every socket timeout is min(60, remaining).
TOTAL_BUDGET_SECONDS = 165
SOCKET_TIMEOUT_MAX = 60
BFL_POLL_INTERVAL_SECONDS = 1.5
ERROR_BODY_CAP = 300
# A provider response that is not an image is tiny; an image is a few MB.
READ_CAP = 32 * 1024 * 1024

# OpenAI's documented custom-size envelope for gpt-image-2+.
OPENAI_GRID = 16
OPENAI_MIN_PIXELS = 655_360
OPENAI_MAX_PIXELS = 8_294_400
OPENAI_MAX_EDGE = 3840

BFL_GRID = 16
# "~1 MP" means 1024x1024 worth of pixels, so a square comes out exactly 1024.
TARGET_PIXELS = 1024 * 1024

DEFAULT_ASPECTS = ["1:1", "3:4", "4:3", "9:16", "16:9"]


class ImageGenError(Exception):
    """An expected, user-facing failure: printed by main() without a
    traceback and turned into exit 1, which the executor relays verbatim."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect. The default handler replays the request —
    Authorization / x-goog-api-key / x-key included — to whatever host a
    3xx Location names, plain http included."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())

# The call's deadline (time.monotonic()); started lazily by the first request
# so library-style callers get the same budget main() does.
_deadline: float | None = None


def start_budget(seconds: float = TOTAL_BUDGET_SECONDS) -> None:
    global _deadline
    _deadline = time.monotonic() + seconds


def remaining() -> float:
    if _deadline is None:
        start_budget()
    return _deadline - time.monotonic()


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

def load_models() -> list[dict]:
    return json.loads(MODELS_PATH.read_text())


def registry() -> dict[str, dict]:
    return {m["id"]: m for m in load_models()}


def default_model_id() -> str:
    models = load_models()
    return next((m["id"] for m in models if m.get("default")), models[0]["id"])


def configured(provider: str, env=os.environ) -> bool:
    return bool(env.get(KEY_ENV[provider][0], "").strip())


def api_key(provider: str, env=os.environ) -> str:
    var, block = KEY_ENV[provider]
    key = env.get(var, "").strip()
    if not key:
        raise ImageGenError(f"provider {provider} is not configured (add the {block} secret)")
    return key


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def _extract_error_message(body_text: str) -> str:
    text = (body_text or "").strip()
    if not text:
        return "(empty response body)"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text[:ERROR_BODY_CAP]
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:ERROR_BODY_CAP]
        if isinstance(err, str) and err:
            return err[:ERROR_BODY_CAP]
        for k in ("detail", "message"):
            if data.get(k):
                return str(data[k])[:ERROR_BODY_CAP]
    return text[:ERROR_BODY_CAP]


def _request(method: str, url: str, provider: str, *, headers: dict | None = None,
             data: bytes | None = None) -> tuple[int, bytes, dict]:
    left = remaining()
    if left <= 0:
        raise ImageGenError(f"{provider}: call budget of {TOTAL_BUDGET_SECONDS} s exhausted")
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with _OPENER.open(req, timeout=min(SOCKET_TIMEOUT_MAX, left)) as resp:
            body = resp.read(READ_CAP + 1)
            if len(body) > READ_CAP:
                raise ImageGenError(f"{provider} response exceeds {READ_CAP // (1024 * 1024)} MiB")
            return (getattr(resp, "status", 200), body,
                    dict(getattr(resp, "headers", {}) or {}))
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            raise ImageGenError(f"{provider} redirected ({e.code}) — refusing to follow "
                                "with credentials")
        try:
            body_text = e.read().decode("utf-8", errors="replace")
        except Exception:
            body_text = ""
        message = _extract_error_message(body_text)
        if e.code in (401, 403):
            raise ImageGenError(f"{provider}: API key invalid or unauthorized "
                                f"(HTTP {e.code}): {message}")
        if e.code == 404:
            raise ImageGenError(f"{provider}: model/endpoint not found (HTTP 404): {message} "
                                "— model ids drift; check models.json against the provider docs")
        if e.code == 429:
            raise ImageGenError(f"{provider}: rate-limited or out of quota (HTTP 429): {message}")
        raise ImageGenError(f"{provider} API error (HTTP {e.code}): {message}")
    except urllib.error.URLError as e:
        raise ImageGenError(f"Could not reach {provider} API: {e.reason}")
    except TimeoutError:
        raise ImageGenError(f"{provider} API request timed out")


def _parse_json(raw: bytes, provider: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise ImageGenError(f"{provider} API returned an unparseable response")


def _post_json(url: str, headers: dict, payload: dict, provider: str) -> dict:
    body = json.dumps(payload).encode("utf-8")
    _, raw, _ = _request("POST", url, provider,
                         headers={**headers, "Content-Type": "application/json"}, data=body)
    return _parse_json(raw, provider)


def _safe_filename(name: str) -> str:
    # A filename lands inside a quoted header value; a quote or line break in
    # it would end the part early and inject headers.
    return "".join("_" if c in '"\r\n' or not c.isprintable() else c for c in name)


def _encode_multipart(fields: list[tuple[str, str]],
                      files: list[tuple[str, str, str, bytes]]) -> tuple[bytes, str]:
    """RFC 2388 body by hand (no `email` package, no third-party): the only
    multipart consumer is OpenAI's edits endpoint."""
    boundary = "----agent-platform-" + secrets.token_hex(16)
    out = bytearray()
    for name, value in fields:
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                f"{value}\r\n").encode("utf-8")
    for name, filename, mime, data in files:
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                f"filename=\"{_safe_filename(filename)}\"\r\nContent-Type: {mime}\r\n\r\n"
                ).encode("utf-8")
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode("utf-8")
    return bytes(out), f"multipart/form-data; boundary={boundary}"


# --------------------------------------------------------------------------
# size / aspect helpers
# --------------------------------------------------------------------------

def parse_size(size: str) -> tuple[int, int]:
    try:
        w, h = str(size).lower().split("x")
        w, h = int(w), int(h)
    except (ValueError, AttributeError):
        raise ImageGenError(f"size must look like WIDTHxHEIGHT, got {size!r}")
    if w <= 0 or h <= 0:
        raise ImageGenError(f"size must be positive, got {size!r}")
    return w, h


def parse_aspect(aspect: str) -> float:
    try:
        a, b = str(aspect).split(":")
        a, b = int(a), int(b)
        if a <= 0 or b <= 0:
            raise ValueError
    except ValueError:
        raise ImageGenError(f"aspect must look like W:H, got {aspect!r}")
    return a / b


def nearest_aspect(w: int, h: int, aspects: list[str] | None = None) -> str:
    target = w / h
    return min(aspects or DEFAULT_ASPECTS, key=lambda a: abs(parse_aspect(a) - target))


def _snap(n: float, grid: int) -> int:
    return max(grid, int(n) // grid * grid)


def aspect_to_size(aspect: str, pixels: int = TARGET_PIXELS,
                   grid: int = 16) -> tuple[int, int]:
    """Pixels for an aspect at about `pixels` total, both edges on the `grid`."""
    ratio = parse_aspect(aspect)
    h = (pixels / ratio) ** 0.5
    return _snap(round(h * ratio), grid), _snap(round(h), grid)


def _openai_size(entry: dict, size: str) -> str:
    if size in entry["sizes"]:
        return size
    if not entry.get("custom_size"):
        raise ImageGenError(f"{entry['id']} only takes size {', '.join(entry['sizes'])}, "
                            f"got {size!r}")
    w, h = parse_size(size)
    if w % OPENAI_GRID or h % OPENAI_GRID:
        raise ImageGenError(f"{entry['id']} custom size must be a multiple of {OPENAI_GRID} "
                            f"on both edges, got {size!r}")
    if not (OPENAI_MIN_PIXELS <= w * h <= OPENAI_MAX_PIXELS) or max(w, h) > OPENAI_MAX_EDGE:
        raise ImageGenError(f"{entry['id']} custom size out of range, got {size!r}")
    if not (1 / 3 <= w / h <= 3):
        raise ImageGenError(f"{entry['id']} custom size aspect must be within 1:3..3:1, got {size!r}")
    return f"{w}x{h}"


def geometry(entry: dict, size: str | None, aspect: str | None) -> dict:
    """The provider-shaped geometry for a model: `{"size": "WxH"}` for a
    sizes-model, `{"aspect": "a:b"}` for an aspects-model — bridged from
    whichever the caller gave (see the module docstring)."""
    if "aspects" in entry:
        if aspect:
            if aspect not in entry["aspects"]:
                raise ImageGenError(f"{entry['id']} takes aspect {', '.join(entry['aspects'])}, "
                                    f"got {aspect!r}")
            return {"aspect": aspect}
        if size:
            w, h = parse_size(size)
            return {"aspect": nearest_aspect(w, h, entry["aspects"])}
        return {"aspect": "1:1" if "1:1" in entry["aspects"] else entry["aspects"][0]}

    if entry["provider"] == "openai":
        if size:
            return {"size": _openai_size(entry, size)}
        if aspect:
            if entry.get("custom_size"):
                w, h = aspect_to_size(aspect, grid=OPENAI_GRID)
                return {"size": f"{w}x{h}"}
            ratio = parse_aspect(aspect)
            return {"size": min(entry["sizes"],
                                key=lambda s: abs(ratio - (lambda p: p[0] / p[1])(parse_size(s))))}
        return {"size": entry["sizes"][0]}

    # BFL width/height models: anything on the 16-grid goes.
    if size:
        w, h = parse_size(size)
        return {"size": f"{_snap(w, BFL_GRID)}x{_snap(h, BFL_GRID)}"}
    if aspect:
        w, h = aspect_to_size(aspect, grid=BFL_GRID)
        return {"size": f"{w}x{h}"}
    return {"size": entry["sizes"][0]}


# --------------------------------------------------------------------------
# reference images
# --------------------------------------------------------------------------

def sniff_mime(head: bytes) -> str | None:
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"GIF8"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def load_references(names: list, in_dir: Path) -> list[tuple[str, bytes]]:
    refs = []
    for name in names or []:
        if not isinstance(name, str) or not name or Path(name).name != name:
            raise ImageGenError(f"reference names must be plain filenames in the call's "
                                f"files_in, got {name!r}")
        p = in_dir / name
        if not p.is_file():
            raise ImageGenError(f"reference {name} was not sent with the call")
        data = p.read_bytes()
        if sniff_mime(data[:16]) is None:
            raise ImageGenError(f"reference {name} is not a png/jpeg/gif/webp image")
        refs.append((name, data))
    return refs


# --------------------------------------------------------------------------
# providers -> (image_bytes, ext[, seed])
# --------------------------------------------------------------------------

def call_openai(model_id: str, prompt: str, size: str, quality: str, key: str,
                references: list[tuple[str, bytes]] | None = None) -> tuple[bytes, str]:
    headers = {"Authorization": f"Bearer {key}"}
    if references:
        fields = [("model", model_id), ("prompt", prompt), ("size", size),
                  ("quality", quality), ("n", "1"), ("output_format", "png")]
        files = [("image[]", name, sniff_mime(data[:16]), data) for name, data in references]
        body, content_type = _encode_multipart(fields, files)
        _, raw, _ = _request("POST", "https://api.openai.com/v1/images/edits", "openai",
                             headers={**headers, "Content-Type": content_type}, data=body)
        body_json = _parse_json(raw, "openai")
    else:
        body_json = _post_json("https://api.openai.com/v1/images/generations", headers,
                               {"model": model_id, "prompt": prompt, "size": size,
                                "quality": quality, "n": 1, "output_format": "png"}, "openai")
    try:
        b64 = body_json["data"][0]["b64_json"]
    except (KeyError, IndexError, TypeError):
        raise ImageGenError(f"openai response missing image data: "
                            f"{json.dumps(body_json)[:ERROR_BODY_CAP]}")
    try:
        return base64.b64decode(b64), "png"
    except Exception:
        raise ImageGenError("openai response image data was not valid base64")


def call_gemini(model_id: str, prompt: str, aspect: str, key: str,
                references: list[tuple[str, bytes]] | None = None,
                image_size: str | None = None, seed: int | None = None) -> tuple[bytes, str]:
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model_id}:generateContent")
    # The key travels in the header: a `?key=` query string lands in logs.
    headers = {"x-goog-api-key": key}
    parts: list[dict] = [{"text": prompt}]
    for _name, data in references or []:
        parts.append({"inlineData": {"mimeType": sniff_mime(data[:16]),
                                     "data": base64.b64encode(data).decode()}})
    image_config: dict = {"aspectRatio": aspect}
    if image_size:
        image_config["imageSize"] = image_size
    generation_config: dict = {"responseModalities": ["TEXT", "IMAGE"],
                               "imageConfig": image_config}
    if seed is not None:
        generation_config["seed"] = seed
    body = _post_json(url, headers, {"contents": [{"parts": parts}],
                                     "generationConfig": generation_config}, "gemini")
    try:
        out_parts = body["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        raise ImageGenError(f"gemini response missing candidates: "
                            f"{json.dumps(body)[:ERROR_BODY_CAP]}")
    for part in out_parts:
        inline = part.get("inlineData") if isinstance(part, dict) else None
        if inline and str(inline.get("mimeType", "")).startswith("image/"):
            ext = str(inline.get("mimeType", "image/png")).split("/")[-1]
            try:
                return base64.b64decode(inline["data"]), "jpg" if ext == "jpeg" else ext
            except Exception:
                raise ImageGenError("gemini response image data was not valid base64")
    raise ImageGenError("gemini response did not contain an image part (the model may have "
                        f"refused): {json.dumps(body)[:ERROR_BODY_CAP]}")


def _bfl_host_ok(url: str) -> bool:
    u = urllib.parse.urlsplit(url)
    host = (u.hostname or "").lower()
    return u.scheme == "https" and (host == "api.bfl.ai" or host.endswith(".bfl.ai"))


def call_bfl(model_id: str, prompt: str, key: str, *, width: int | None = None,
             height: int | None = None, aspect: str | None = None, seed: int | None = None,
             references: list[tuple[str, bytes]] | None = None) -> tuple[bytes, str, int | None]:
    """BFL is async: submit -> poll polling_url -> download result.sample.
    FLUX.2 and FLUX 1.1 take width/height; Kontext takes aspect_ratio.
    The provider names the URLs to poll and download: the key goes only to
    an https host under bfl.ai, and the sample is fetched over https only."""
    headers = {"x-key": key, "accept": "application/json"}
    payload: dict = {"prompt": prompt, "output_format": "png"}
    if aspect:
        payload["aspect_ratio"] = aspect
    else:
        payload["width"], payload["height"] = width, height
    if seed is not None:
        payload["seed"] = seed
    for i, (_name, data) in enumerate(references or []):
        payload["input_image" if i == 0 else f"input_image_{i + 1}"] = \
            base64.b64encode(data).decode()
    submit = _post_json(f"https://api.bfl.ai/v1/{model_id}", headers, payload, "bfl")
    poll_url = submit.get("polling_url")
    if not poll_url:
        raise ImageGenError(f"bfl submit returned no polling_url: "
                            f"{json.dumps(submit)[:ERROR_BODY_CAP]}")
    if not _bfl_host_ok(str(poll_url)):
        raise ImageGenError("bfl returned an unexpected polling host")
    while remaining() > 0:
        _, raw, _ = _request("GET", poll_url, "bfl", headers=headers)
        res = _parse_json(raw, "bfl")
        status = str(res.get("status", ""))
        if status == "Ready":
            result = res.get("result") or {}
            sample = result.get("sample")
            if not sample:
                raise ImageGenError(f"bfl ready but no image url: {json.dumps(res)[:ERROR_BODY_CAP]}")
            if urllib.parse.urlsplit(str(sample)).scheme != "https":
                raise ImageGenError("bfl returned a non-https sample url")
            # The sample is a signed delivery URL: no key goes with it.
            _, img, hdrs = _request("GET", sample, "bfl")
            ctype = str(hdrs.get("Content-Type", "")).lower()
            ext = "jpg" if "jpeg" in ctype or sniff_mime(img[:16]) == "image/jpeg" else "png"
            out_seed = result.get("seed")
            return img, ext, out_seed if isinstance(out_seed, int) else seed
        if status in ("Error", "Failed"):
            raise ImageGenError(f"bfl generation failed: {json.dumps(res)[:ERROR_BODY_CAP]}")
        if status in ("Content Moderated", "Request Moderated"):
            raise ImageGenError("bfl refused the request (content moderation)")
        time.sleep(BFL_POLL_INTERVAL_SECONDS)
    raise ImageGenError(f"bfl: still generating after {TOTAL_BUDGET_SECONDS} s")


# --------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------

def _quality(entry: dict, quality) -> str | None:
    if quality is None or quality == "":
        return DEFAULT_QUALITY.get(entry["provider"]) if entry["qualities"] else None
    if not entry["qualities"]:
        raise ImageGenError(f"{entry['id']} takes no quality setting")
    if quality not in entry["qualities"]:
        raise ImageGenError(f"{entry['id']} takes quality {', '.join(entry['qualities'])}, "
                            f"got {quality!r}")
    return quality


def _seed(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ImageGenError(f"seed must be an integer, got {value!r}")
    return value


def generate(args: dict, env=os.environ) -> dict:
    reg = registry()
    model_id = args.get("model") or default_model_id()
    entry = reg.get(model_id)
    if entry is None:
        raise ImageGenError(f"unknown model {model_id!r}; action=models lists the registry")
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        raise ImageGenError("prompt is required")
    quality = _quality(entry, args.get("quality"))
    seed = _seed(args.get("seed"))
    in_dir = Path(env.get("TOOL_IN_DIR", ""))
    out_dir = Path(env.get("TOOL_OUT_DIR", ""))
    if not env.get("TOOL_OUT_DIR") or not out_dir.is_dir():
        raise ImageGenError("TOOL_OUT_DIR is not set — the tool must run under the executor")
    references = load_references(args.get("references"), in_dir)
    if references and not entry["edits"]:
        raise ImageGenError(f"{entry['id']} does not accept reference images")

    geo = geometry(entry, args.get("size") or None, args.get("aspect") or None)
    params = {"prompt": prompt}
    for k in ("size", "aspect"):
        if args.get(k):
            params[k] = args[k]
    params.update(geo)
    if quality is not None:
        params["quality"] = quality
    params["references"] = [name for name, _ in references]

    provider = entry["provider"]
    key = api_key(provider, env)
    warnings: list[str] = []
    started = time.perf_counter()
    out_seed = seed
    if provider == "openai":
        data, ext = call_openai(model_id, prompt, geo["size"], quality, key, references)
        out_seed = None
        if seed is not None:
            warnings.append("seed ignored by openai")
    elif provider == "gemini":
        data, ext = call_gemini(model_id, prompt, geo["aspect"], key, references,
                                image_size=quality, seed=seed)
    elif provider == "bfl":
        if "aspect" in geo:
            data, ext, out_seed = call_bfl(model_id, prompt, key, aspect=geo["aspect"],
                                           seed=seed, references=references)
        else:
            w, h = parse_size(geo["size"])
            data, ext, out_seed = call_bfl(model_id, prompt, key, width=w, height=h,
                                           seed=seed, references=references)
    else:
        raise ImageGenError(f"internal: unknown provider {provider!r}")
    duration_ms = int((time.perf_counter() - started) * 1000)

    filename = f"image.{ext}"
    meta = {"provider": provider, "model": model_id, "seed": out_seed,
            "cost_usd": entry["price_usd"], "duration_ms": duration_ms, "params": params,
            "warnings": warnings}
    try:
        (out_dir / filename).write_bytes(data)
        (out_dir / f"{filename}.meta.json").write_text(json.dumps(meta))
    except OSError as e:
        raise ImageGenError(f"could not write {filename} to TOOL_OUT_DIR: {e}")
    return {"ok": True, "model": model_id, "cost_usd": entry["price_usd"]}


def models(env=os.environ) -> dict:
    providers = {p: {"configured": configured(p, env), "secret": block}
                 for p, (_var, block) in KEY_ENV.items()}
    return {
        "default": default_model_id(),
        "providers": providers,
        "models": [{**m, "configured": providers[m["provider"]]["configured"]}
                   for m in load_models()],
    }


def main(args: dict | None = None) -> int:
    start_budget()
    try:
        if args is None:
            args = json.load(sys.stdin)
        action = args.get("action") or "generate"
        if action == "generate":
            print(json.dumps(generate(args)))
        elif action == "models":
            print(json.dumps(models()))
        else:
            raise ImageGenError(f"unknown action {action!r} (generate | models)")
    except ImageGenError as e:
        print(f"image_gen: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        # The executor relays stderr to the browser: a traceback there would
        # be noise at best and env-shaped at worst.
        print(f"image_gen: unexpected {type(e).__name__}: {str(e)[:ERROR_BODY_CAP]}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
