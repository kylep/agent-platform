"""The `artifacts` and `image_gen` broker tools (docs/design/23 T7): what each
action does to the platform API, and what comes back.

The same shape as `test_wiki_tool.py`, with one difference that is the point
of these two tools: an answer can carry a PICTURE. So the fake API sits one
level lower — at `_request`, which every core tool speaks through and which
is the one place the caller's headers are stamped on — and answers with
status, body and content-type, so the byte routes and the JSON routes are
faked by the same table. The fastmcp and `mcp.types` stubs and the loaded
broker module come from `test_relay_tool.py`.

    cd services/mcp-broker && ../backend/.venv/bin/python -m pytest -q test_artifacts_tool.py

Tests are synchronous and drive the coroutines with `asyncio.run`."""
import asyncio
import base64
import inspect
import json
from datetime import datetime, timezone

import httpx
import pytest
# `caller` is an autouse fixture: importing it registers it for this module
# too, so every call resolves an identity and starts with a full bucket.
from test_relay_tool import (Calls, broker, caller,  # noqa: F401
                             published)

IMAGE_ID = "ab" * 16
FILE_ID = "cd" * 16
ARTIFACTS = "/api/artifacts"
NOW = datetime(2026, 9, 17, 11, 0, 0, tzinfo=timezone.utc)
HINT = f"reference it in Relay as `[[artifact:{IMAGE_ID}]]`"

# Two rows as /api/artifacts answers them: a generated image and an uploaded
# file, with the fields only the web UI reads left in.
IMAGE = {"id": IMAGE_ID, "name": "sunset.png", "mime": "image/png", "size": 48213,
         "sha256": "0" * 64, "kind": "image", "width": 1024, "height": 768,
         "owner": "agent:artist", "run_id": "r1", "source": "generated",
         "meta": {"model": "gpt-image-1", "provider": "openai", "prompt": "a sunset",
                  "cost_usd": 0.04, "seed": 7, "params": {}, "reference_ids": []},
         "tags": ["art"], "created_at": "2026-09-17T09:00:00+00:00", "deleted_at": None,
         "thumb_url": f"{ARTIFACTS}/{IMAGE_ID}/thumb",
         "content_url": f"{ARTIFACTS}/{IMAGE_ID}/content"}
FILE = {**IMAGE, "id": FILE_ID, "name": "prices.csv", "mime": "text/csv", "size": 1200,
        "kind": "file", "width": None, "height": None, "owner": "user:kyle",
        "source": "upload", "meta": {}, "tags": [], "thumb_url": None,
        "content_url": f"{ARTIFACTS}/{FILE_ID}/content"}
THUMB = b"\xff\xd8\xff" + b"t" * 40           # a JPEG, as the store makes them
ORIGINAL = b"\x89PNG" + b"o" * 100
MODELS = [{"id": "gpt-image-1", "provider": "openai", "label": "GPT Image 1",
           "price_usd": 0.04, "sizes": ["1024x1024", "1536x1024"], "aspects": None,
           "custom_size": False, "qualities": ["low", "medium", "high"], "edits": True,
           "configured": True, "default": True},
          {"id": "flux-pro", "provider": "bfl", "label": "FLUX Pro", "price_usd": 0.05,
           "sizes": None, "aspects": ["1:1", "16:9"], "custom_size": True,
           "qualities": None, "edits": False, "configured": False, "default": False}]


class FakeResponse:
    def __init__(self, status, body, mime="application/json"):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else body.encode()
        self.text = body if isinstance(body, str) else body.decode("latin-1")
        self.headers = {"content-type": mime}


@pytest.fixture
def api(monkeypatch):
    """The fake platform API, keyed by path: `replies[path]` is (status, body)
    or (status, body, content-type); the default is a JSON ok."""
    recorded = Calls()
    recorded.replies[f"{ARTIFACTS}/{IMAGE_ID}"] = (200, json.dumps(IMAGE))
    recorded.replies[f"{ARTIFACTS}/{FILE_ID}"] = (200, json.dumps(FILE))
    recorded.replies[f"{ARTIFACTS}/{IMAGE_ID}/thumb"] = (200, THUMB, "image/jpeg")
    recorded.replies[f"{ARTIFACTS}/{IMAGE_ID}/content"] = (200, ORIGINAL, "image/png")

    # The parameter names are broker._request's own — the tools pass `json=`
    # by keyword, and a stub that renamed it would pass a test the real call
    # fails. The timeout is recorded beside the call, not in it, so the
    # request-shape assertions stay four-tuples.
    async def _request(method, path, params=None, json=None, timeout=20):
        recorded.append((method, path, params, json))
        recorded.timeouts.append((path, timeout))
        reply = recorded.replies.get(path, (200, '{"ok": true}'))
        if isinstance(reply, Exception):
            raise reply
        return FakeResponse(*reply)

    recorded.timeouts = []

    monkeypatch.setattr(broker, "_request", _request)
    monkeypatch.setattr(broker, "_quota_now", lambda: NOW)
    return recorded


def artifacts(**kw):
    return asyncio.run(broker.artifacts(**kw))


def image_gen(**kw):
    return asyncio.run(broker.image_gen(**kw))


def text_of(out) -> str:
    """The words in an answer, whether it came back bare or with a picture."""
    if isinstance(out, str):
        return out
    return "\n".join(b.text for b in out.content if getattr(b, "text", None) is not None)


def images_of(out) -> list:
    return [] if isinstance(out, str) else [
        b for b in out.content if getattr(b, "data", None) is not None]


# --- list ---------------------------------------------------------------------

def test_list_is_one_line_per_artifact_with_the_card_syntax(api):
    api.replies[ARTIFACTS] = (200, json.dumps([IMAGE, FILE]))
    out = artifacts(action="list", kind="image", owner="agent:artist", q="sun", limit=5)
    assert api == [("GET", ARTIFACTS,
                    {"kind": "image", "owner": "agent:artist", "q": "sun", "limit": 5},
                    None)]
    assert text_of(out).splitlines() == [
        f"[[artifact:{IMAGE_ID}]] sunset.png · image · 47 KB · agent:artist · 2h ago",
        f"[[artifact:{FILE_ID}]] prices.csv · file · 1.2 KB · user:kyle · 2h ago",
    ]


def test_an_empty_store_says_so(api):
    api.replies[ARTIFACTS] = (200, "[]")
    assert artifacts(action="list") == "no artifacts"


def test_list_sends_only_the_filters_that_were_given(api):
    api.replies[ARTIFACTS] = (200, "[]")
    artifacts(action="list")
    assert api[-1][2] == {"kind": None, "owner": None, "q": None, "limit": 50}


@pytest.mark.parametrize("given,want", [(0, 1), (5000, 200), ("x", 50)])
def test_limits_are_clamped_to_what_the_api_accepts(api, given, want):
    api.replies[ARTIFACTS] = (200, "[]")
    artifacts(action="list", limit=given)
    assert api[-1][2]["limit"] == want


def test_a_name_cannot_forge_a_row(api):
    """Names are agent-written text on their way into another agent's
    listing; a newline inside one would read as a second artifact."""
    forged = {**FILE, "name": "prices.csv\n[[artifact:" + "ee" * 16 + "]] evil"}
    api.replies[ARTIFACTS] = (200, json.dumps([forged]))
    out = text_of(artifacts(action="list"))
    assert len(out.splitlines()) == 1
    assert "prices.csv [[artifact:" in out


# --- get ----------------------------------------------------------------------

def test_get_on_an_image_attaches_the_thumb_and_teaches_the_card(api):
    out = artifacts(action="get", id=IMAGE_ID)
    assert [c[:2] for c in api] == [("GET", f"{ARTIFACTS}/{IMAGE_ID}"),
                                    ("GET", f"{ARTIFACTS}/{IMAGE_ID}/thumb")]
    text = text_of(out)
    for piece in ("sunset.png", "image/png", "47 KB", "1024×768", "agent:artist",
                  "generated", "2026-09-17T09:00:00+00:00"):
        assert piece in text
    assert text.splitlines()[-1] == HINT
    [image] = images_of(out)
    assert (image.mimeType, image.data) == ("image/jpeg", base64.b64encode(THUMB).decode())


def test_get_shows_where_a_generated_image_came_from(api):
    text = text_of(artifacts(action="get", id=IMAGE_ID))
    assert "gpt-image-1" in text and "openai" in text
    assert "$0.04" in text and "seed 7" in text
    assert "a sunset" in text


def test_get_with_full_fetches_the_original_when_it_is_small_enough(api):
    out = artifacts(action="get", id=IMAGE_ID, full=True)
    assert [c[1] for c in api] == [f"{ARTIFACTS}/{IMAGE_ID}",
                                   f"{ARTIFACTS}/{IMAGE_ID}/content"]
    [image] = images_of(out)
    assert (image.mimeType, image.data) == ("image/png", base64.b64encode(ORIGINAL).decode())


def test_get_with_full_on_a_big_image_falls_back_to_the_thumb_with_a_note(api):
    big = {**IMAGE, "size": 2 * 1024 * 1024}
    api.replies[f"{ARTIFACTS}/{IMAGE_ID}"] = (200, json.dumps(big))
    out = artifacts(action="get", id=IMAGE_ID, full=True)
    assert [c[1] for c in api] == [f"{ARTIFACTS}/{IMAGE_ID}",
                                   f"{ARTIFACTS}/{IMAGE_ID}/thumb"]
    [image] = images_of(out)
    assert image.mimeType == "image/jpeg"
    text = text_of(out)
    assert "2.0 MB" in text and "thumb" in text.lower()
    assert text.splitlines()[-1] == HINT


def test_get_on_a_file_is_metadata_and_no_picture(api):
    out = artifacts(action="get", id=FILE_ID)
    assert [c[1] for c in api] == [f"{ARTIFACTS}/{FILE_ID}"]
    assert images_of(out) == []
    text = text_of(out)
    assert "prices.csv" in text and "text/csv" in text and "1.2 KB" in text
    assert text.splitlines()[-1] == f"reference it in Relay as `[[artifact:{FILE_ID}]]`"


def test_a_thumb_the_api_will_not_serve_still_answers_the_metadata(api):
    api.replies[f"{ARTIFACTS}/{IMAGE_ID}/thumb"] = (404, '{"detail":"no thumb for this artifact"}')
    out = artifacts(action="get", id=IMAGE_ID)
    assert images_of(out) == []
    text = text_of(out)
    assert "sunset.png" in text and "no thumb" in text
    assert text.splitlines()[-1] == HINT


def test_a_picture_that_is_not_an_image_is_not_attached(api):
    """The API's own content-type is the second opinion on what came back:
    a byte route that answered with HTML — a login page, a proxy error — is
    text the model must not be shown as a picture."""
    api.replies[f"{ARTIFACTS}/{IMAGE_ID}/thumb"] = (200, "<html>sign in</html>", "text/html")
    out = artifacts(action="get", id=IMAGE_ID)
    assert images_of(out) == []
    text = text_of(out)
    assert "text/html" in text and "sign in" not in text
    assert text.splitlines()[-1] == HINT


def test_a_picture_bigger_than_the_route_may_serve_is_not_attached(api):
    """The size the metadata claimed is checked again on the bytes that
    arrived: a thumb is at most 150 KiB and an original at most the 1 MiB
    `full` promised, whatever the row said."""
    api.replies[f"{ARTIFACTS}/{IMAGE_ID}/thumb"] = (200, b"\xff\xd8" + b"t" * (150 * 1024 + 1), "image/jpeg")
    out = artifacts(action="get", id=IMAGE_ID)
    assert images_of(out) == []
    assert "150 KB" in text_of(out)
    api.replies[f"{ARTIFACTS}/{IMAGE_ID}/content"] = (200, b"\x89PNG" + b"o" * (1024 * 1024 + 1), "image/png")
    out = artifacts(action="get", id=IMAGE_ID, full=True)
    assert images_of(out) == []
    assert "1.0 MB" in text_of(out)


def test_a_long_prompt_is_shortened_in_the_provenance(api):
    long_row = {**IMAGE, "meta": {**IMAGE["meta"], "prompt": "p" * 1000}}
    api.replies[f"{ARTIFACTS}/{IMAGE_ID}"] = (200, json.dumps(long_row))
    line = next(l for l in text_of(artifacts(action="get", id=IMAGE_ID)).splitlines()
                if l.startswith("generated by"))
    assert len(line) < 400 and line.endswith("…")


@pytest.mark.parametrize("given", [IMAGE_ID, f"[[artifact:{IMAGE_ID}]]", f"`{IMAGE_ID}`",
                                   f"artifact:{IMAGE_ID}"])
def test_an_id_the_model_wrapped_in_the_card_syntax_is_still_the_id(api, given):
    artifacts(action="get", id=given)
    assert api[0][1] == f"{ARTIFACTS}/{IMAGE_ID}"


@pytest.mark.parametrize("given", ["x/../../whoami", "../runs", "sunset.png", IMAGE_ID.upper(), ""])
def test_an_id_that_could_escape_the_tool_is_never_a_path(api, given):
    """The id is interpolated into the path and httpx normalises `..` before
    the request leaves, so anything that is not an id is refused here rather
    than spent on whatever endpoint it turned out to name."""
    out = artifacts(action="get", id=given)
    assert out.startswith("error:") and "id" in out
    assert api == []


def test_get_passes_the_apis_refusal_back_in_plain_words(api):
    api.replies[f"{ARTIFACTS}/{IMAGE_ID}"] = (404, '{"detail":"unknown artifact"}')
    assert artifacts(action="get", id=IMAGE_ID) == "error: unknown artifact"


# --- save ---------------------------------------------------------------------

def test_save_text_posts_json_and_answers_with_the_card(api):
    api.replies[ARTIFACTS] = (201, json.dumps(FILE))
    out = artifacts(action="save", name="prices.csv", text="a,b\n1,2", tags=["data"])
    assert api == [("POST", ARTIFACTS, None,
                    {"name": "prices.csv", "text": "a,b\n1,2", "tags": ["data"]})]
    assert out.splitlines() == [
        f"saved [[artifact:{FILE_ID}]] prices.csv · file · 1.2 KB",
        f"reference it in Relay as `[[artifact:{FILE_ID}]]`"]


def test_save_bytes_posts_the_base64_it_was_given(api):
    api.replies[ARTIFACTS] = (201, json.dumps(IMAGE))
    b64 = base64.b64encode(ORIGINAL).decode()
    artifacts(action="save", name="sunset.png", content_b64=b64)
    assert api == [("POST", ARTIFACTS, None, {"name": "sunset.png", "content_b64": b64})]


def test_save_carries_a_mime_claim_only_when_one_was_made(api):
    api.replies[ARTIFACTS] = (201, json.dumps(FILE))
    artifacts(action="save", name="notes.md", text="# hi", mime="text/markdown")
    assert api[-1][3] == {"name": "notes.md", "text": "# hi", "mime": "text/markdown"}


def test_save_refuses_bytes_over_the_limit_before_posting(api):
    b64 = base64.b64encode(b"x" * (256 * 1024 + 1)).decode()
    out = artifacts(action="save", name="big.bin", content_b64=b64)
    assert out.startswith("error:") and "256 KiB" in out
    assert api == []


def test_save_refuses_base64_that_does_not_decode(api):
    out = artifacts(action="save", name="x.bin", content_b64="not base64!!")
    assert out.startswith("error:") and "base64" in out
    assert api == []


def test_save_needs_a_name(api):
    out = artifacts(action="save", text="hello")
    assert out.startswith("error:") and "name" in out
    assert api == []


@pytest.mark.parametrize("kw", [{}, {"text": "a", "content_b64": "YQ=="}])
def test_save_takes_exactly_one_body(api, kw):
    out = artifacts(action="save", name="x.txt", **kw)
    assert out.startswith("error:") and "text" in out and "content_b64" in out
    assert api == []


def test_tags_may_be_a_comma_separated_string(api):
    api.replies[ARTIFACTS] = (201, json.dumps(FILE))
    artifacts(action="save", name="x.txt", text="a", tags="data, q3")
    assert api[-1][3]["tags"] == ["data", "q3"]


def test_a_413_is_plain_words_too(api):
    api.replies[ARTIFACTS] = (413, '{"detail":"an upload is at most 8388608 bytes on the wire"}')
    out = artifacts(action="save", name="x.txt", text="a")
    assert out == "error: an upload is at most 8388608 bytes on the wire"


# --- delete -------------------------------------------------------------------

def test_delete_is_one_call(api):
    api.replies[f"{ARTIFACTS}/{FILE_ID}"] = (200, json.dumps({**FILE, "deleted_at": "2026-09-17T11:00:00+00:00"}))
    out = artifacts(action="delete", id=FILE_ID)
    assert api == [("DELETE", f"{ARTIFACTS}/{FILE_ID}", None, None)]
    assert out == f"deleted [[artifact:{FILE_ID}]] prices.csv"


def test_delete_of_somebody_elses_is_the_apis_refusal(api):
    api.replies[f"{ARTIFACTS}/{FILE_ID}"] = (
        403, '{"detail":"only the owner, an agents_edit holder or the admin may change this"}')
    out = artifacts(action="delete", id=FILE_ID)
    assert out == "error: only the owner, an agents_edit holder or the admin may change this"


def test_an_unknown_action_lists_the_real_ones(api):
    out = artifacts(action="rename")
    assert out.startswith("error:") and "list|get|save|delete" in out
    assert api == []


@pytest.mark.parametrize("action", ["get", "delete"])
def test_every_id_action_needs_an_id(api, action):
    out = artifacts(action=action)
    assert out.startswith("error:") and "id" in out
    assert api == []


# --- image_gen ----------------------------------------------------------------

def test_generate_posts_the_request_and_shows_the_artist_its_work(api):
    api.replies[f"{ARTIFACTS}/generate"] = (201, json.dumps(IMAGE))
    out = image_gen(action="generate", prompt="a sunset", model="gpt-image-1",
                    size="1024x1024", quality="high", seed=7, reference_ids=[FILE_ID],
                    name="sunset", tags=["art"])
    assert api[0] == ("POST", f"{ARTIFACTS}/generate", None,
                      {"prompt": "a sunset", "model": "gpt-image-1", "size": "1024x1024",
                       "quality": "high", "seed": 7, "reference_ids": [FILE_ID],
                       "name": "sunset", "tags": ["art"]})
    assert api[1][:2] == ("GET", f"{ARTIFACTS}/{IMAGE_ID}/thumb")
    assert text_of(out).splitlines() == [
        f"[[artifact:{IMAGE_ID}]] sunset.png · gpt-image-1 · 1024×768 · $0.04 · seed 7",
        HINT]
    [image] = images_of(out)
    assert (image.mimeType, image.data) == ("image/jpeg", base64.b64encode(THUMB).decode())


def test_generate_sends_only_what_was_given(api):
    api.replies[f"{ARTIFACTS}/generate"] = (201, json.dumps(IMAGE))
    image_gen(action="generate", prompt="a sunset", aspect="16:9")
    assert api[0][3] == {"prompt": "a sunset", "aspect": "16:9"}


def test_generate_says_seed_unknown_when_the_provider_gave_none_and_repeats_warnings(api):
    row = {**IMAGE, "meta": {**IMAGE["meta"], "seed": None,
                             "warnings": ["size 1536x1024 bridged to aspect 3:2"]}}
    api.replies[f"{ARTIFACTS}/generate"] = (201, json.dumps(row))
    lines = text_of(image_gen(action="generate", prompt="a sunset")).splitlines()
    assert lines[0].endswith("· seed —")
    assert lines[-1] == "warning: size 1536x1024 bridged to aspect 3:2"


def test_generate_waits_as_long_as_the_api_may_take(api):
    """The generate route is synchronous for up to ~210 s. With the broker's
    20 s default the call would time out, the model would retry, and the
    ORIGINAL generation would keep running server-side — paid for, and posted
    as a card, once per retry."""
    api.replies[f"{ARTIFACTS}/generate"] = (201, json.dumps(IMAGE))
    image_gen(action="generate", prompt="a sunset")
    assert dict(api.timeouts) == {f"{ARTIFACTS}/generate": 240,
                                  f"{ARTIFACTS}/{IMAGE_ID}/thumb": 20}
    api.replies[f"{ARTIFACTS}/models"] = (200, "[]")
    image_gen(action="models")
    artifacts(action="get", id=IMAGE_ID)
    assert all(t == 20 for path, t in api.timeouts if not path.endswith("/generate"))


def test_a_generate_that_times_out_says_not_to_retry_blindly(api, published):
    api.replies[f"{ARTIFACTS}/generate"] = httpx.ReadTimeout("read timed out")
    out = image_gen(action="generate", prompt="a sunset")
    assert out == ("error: the generator did not answer within 240 s; do NOT retry "
                   "blindly — check `artifacts list` for a result first")
    assert published[-1][1]["data"]["decision"] == "error:tool"


def test_the_request_hands_its_timeout_to_httpx(monkeypatch):
    seen = []
    real = httpx.AsyncClient

    def client(**kw):
        seen.append(kw.get("timeout"))
        return real(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")), **kw)

    monkeypatch.setattr(broker.httpx, "AsyncClient", client)
    monkeypatch.setattr(broker, "_caller_headers", lambda: {})
    asyncio.run(broker._request("GET", "/api/artifacts"))
    asyncio.run(broker._request("POST", "/api/artifacts/generate", timeout=240))
    assert seen == [20, 240]


def test_generate_takes_at_most_four_references(api):
    out = image_gen(action="generate", prompt="x", reference_ids=[IMAGE_ID] * 5)
    assert out.startswith("error:") and "four" in out
    assert api == []


def test_generate_needs_a_prompt(api):
    out = image_gen(action="generate")
    assert out.startswith("error:") and "prompt" in out
    assert api == []


def test_generate_reference_ids_are_gated_like_every_id(api):
    out = image_gen(action="generate", prompt="x", reference_ids=["../runs"])
    assert out.startswith("error:") and "id" in out
    assert api == []


@pytest.mark.parametrize("status,detail,tail", [
    (429, "image budget: wait 12m 3s", "wait that long, then retry"),
    (402, "daily image spend cap reached ($5.00)", "try again tomorrow"),
    (502, "image generator refused the request (400)", "change the prompt or the model"),
])
def test_a_refusal_is_plain_words_the_model_can_act_on(api, status, detail, tail):
    api.replies[f"{ARTIFACTS}/generate"] = (status, json.dumps({"detail": detail}))
    out = image_gen(action="generate", prompt="a sunset")
    assert isinstance(out, str)
    assert out.startswith(f"error: {detail}") and tail in out
    assert str(status) not in out.split(detail)[0]


def test_a_refusal_that_is_not_json_is_cut_short(api):
    api.replies[f"{ARTIFACTS}/generate"] = (502, "<html>" + "x" * 2000 + "</html>", "text/html")
    out = image_gen(action="generate", prompt="a sunset")
    assert out.startswith("error: <html>") and len(out) < 400


def test_a_422_names_the_field(api):
    api.replies[f"{ARTIFACTS}/generate"] = (422, json.dumps(
        {"detail": [{"loc": ["body", "size"], "msg": "not a size gpt-image-1 takes"}]}))
    out = image_gen(action="generate", prompt="a sunset", size="7x7")
    assert out == "error: size: not a size gpt-image-1 takes"


def test_models_is_one_line_per_model(api):
    api.replies[f"{ARTIFACTS}/models"] = (200, json.dumps(MODELS))
    out = image_gen(action="models")
    assert api == [("GET", f"{ARTIFACTS}/models", None, None)]
    lines = out.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("gpt-image-1 · openai · GPT Image 1 · $0.04")
    assert "1024x1024|1536x1024" in lines[0] and "default" in lines[0]
    assert "edits" in lines[0]
    assert "16:9" in lines[1] and "not configured" in lines[1]


def test_no_model_configured_says_so(api):
    api.replies[f"{ARTIFACTS}/models"] = (200, "[]")
    assert image_gen(action="models") == "no image models are registered"


def test_image_gen_unknown_action_lists_the_real_ones(api):
    out = image_gen(action="draw")
    assert out.startswith("error:") and "generate|models" in out


# --- authorship is the token, never an argument ------------------------------

EVERY_ACTION = [
    (artifacts, {"action": "list"}),
    (artifacts, {"action": "get", "id": IMAGE_ID}),
    (artifacts, {"action": "save", "name": "x", "text": "y"}),
    (artifacts, {"action": "delete", "id": FILE_ID}),
    (image_gen, {"action": "generate", "prompt": "x"}),
    (image_gen, {"action": "models"}),
]


def test_no_action_can_claim_to_be_somebody_else(api):
    """The owner of a save and the payer of a generation are the forwarded
    bearer. `owner` exists as a READ filter on `list` and nowhere else: no
    body ever carries one, whatever the model passed."""
    api.replies[ARTIFACTS] = (201, json.dumps(FILE))
    api.replies[f"{ARTIFACTS}/generate"] = (201, json.dumps(IMAGE))
    for call, kwargs in EVERY_ACTION:
        call(**{**kwargs, "owner": "agent:admin"} if call is artifacts else kwargs)
    for method, path, params, body in api:
        if body:
            assert not {"owner", "author", "agent", "run_id"} & set(body), (method, path)
        if params and method != "GET":
            assert "owner" not in params, (method, path)


def test_the_tools_take_no_authorship_argument():
    for fn in (broker.artifacts, broker.image_gen):
        names = set(inspect.signature(fn).parameters)
        assert not names & {"author", "as_agent", "agent", "run_id"}
    assert "owner" not in inspect.signature(broker.image_gen).parameters


def test_the_bytes_go_out_with_the_callers_own_headers(monkeypatch):
    """`_bytes` reaches the API through `_request`, the one function that
    stamps the caller's bearer and run token on — so the thumb is fetched as
    the agent, never as the broker (which has no credential to fetch it with)."""
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["run"] = request.headers.get("x-ap-run-token")
        seen["url"] = str(request.url)
        return httpx.Response(200, content=THUMB, headers={"content-type": "image/jpeg"})

    real = httpx.AsyncClient
    monkeypatch.setattr(broker.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(broker, "_caller_headers",
                        lambda: {"Authorization": "Bearer t0k", "X-AP-Run-Token": "jwt"})
    data, mime, error = asyncio.run(
        broker._bytes(f"{ARTIFACTS}/{IMAGE_ID}/thumb", broker.ARTIFACT_THUMB_MAX))
    assert (data, mime, error) == (THUMB, "image/jpeg", None)
    assert (seen["auth"], seen["run"]) == ("Bearer t0k", "jwt")
    assert seen["url"].endswith(f"{ARTIFACTS}/{IMAGE_ID}/thumb")


# --- the grant ------------------------------------------------------------------
# `image_gen` costs money and is granted to the artist alone; `artifacts` is
# default-granted but can be taken away. The API re-derives both from the
# token, and the broker says so first — with an audit row for the attempt.

@pytest.fixture
def ungranted(monkeypatch):
    async def _whoami():
        return {"agent": "news", "run_id": "r1", "initiated_by": "cron",
                "tools": ["mcp__platform__relay", "mcp__platform__artifacts"]}

    monkeypatch.setattr(broker, "_whoami", _whoami)


def test_an_agent_not_granted_image_gen_is_refused_before_the_api(api, published, ungranted):
    out = image_gen(action="generate", prompt="a sunset")
    assert out == "error: your agent does not declare the image_gen tool"
    assert api == []
    assert published[-1][1]["data"]["decision"] == "deny:undeclared"


def test_the_artifacts_grant_it_does_hold_still_works(api, ungranted):
    api.replies[ARTIFACTS] = (200, "[]")
    assert artifacts(action="list") == "no artifacts"


def test_a_caller_with_no_declared_list_is_the_apis_to_judge(api, monkeypatch):
    """A human's key has no `tools` — the role is the authority, as everywhere."""
    async def _whoami():
        return {"principal": "kyle"}

    monkeypatch.setattr(broker, "_whoami", _whoami)
    api.replies[f"{ARTIFACTS}/models"] = (200, "[]")
    assert image_gen(action="models") == "no image models are registered"


# --- rate limit + audit trail --------------------------------------------------

def test_every_call_lands_in_the_audit_trail_with_its_action(api, published):
    artifacts(action="get", id=IMAGE_ID)
    assert len(published) == 1
    data = published[0][1]["data"]
    assert (data["tool"], data["action"], data["decision"]) == ("artifacts", "get", "allow")
    assert (data["agent"], data["run_id"]) == ("news", "r1")


def test_the_audit_row_counts_the_picture_and_never_carries_it(api, published):
    """Sizes, never bytes: the row says how much left, and a thumb that went to
    the model is not a thumb that went to Kafka."""
    out = artifacts(action="get", id=IMAGE_ID)
    envelope = published[0][1]
    assert envelope["data"]["result_bytes"] == len(text_of(out)) + len(THUMB)
    assert base64.b64encode(THUMB).decode() not in json.dumps(envelope)


def test_the_audit_row_digests_what_was_saved(api, published):
    api.replies[ARTIFACTS] = (201, json.dumps(FILE))
    artifacts(action="save", name="x.txt", text="the password is hunter2")
    envelope = published[0][1]
    assert "hunter2" not in json.dumps(envelope)
    assert len(envelope["data"]["args_digest"]) == 64


def test_generate_is_named_in_its_own_record(api, published):
    api.replies[f"{ARTIFACTS}/generate"] = (201, json.dumps(IMAGE))
    image_gen(action="generate", prompt="a sunset")
    data = published[-1][1]["data"]
    assert (data["tool"], data["action"], data["decision"]) == ("image_gen", "generate", "allow")


def test_a_refusal_the_tool_answers_itself_is_still_audited(api, published):
    assert artifacts(action="get").startswith("error:")
    assert published[-1][1]["data"]["decision"] == "error:tool"


def test_over_the_limit_is_refused_without_touching_the_api(api, published):
    api.replies[ARTIFACTS] = (200, "[]")
    for _ in range(int(broker._RATE_CAPACITY)):
        assert not artifacts(action="list").startswith("error:")
    made = len(api)
    assert artifacts(action="list").startswith("error: rate limit")
    assert len(api) == made
    assert published[-1][1]["data"]["decision"] == "deny:rate-limit"


def test_the_two_tools_are_counted_apart(api):
    api.replies[ARTIFACTS] = (200, "[]")
    api.replies[f"{ARTIFACTS}/models"] = (200, "[]")
    for _ in range(int(broker._RATE_CAPACITY) + 1):
        artifacts(action="list")
    assert artifacts(action="list").startswith("error: rate limit")
    assert not image_gen(action="models").startswith("error:")


def test_metering_keeps_the_signatures_fastmcp_turns_into_schemas():
    params = inspect.signature(broker.artifacts).parameters
    assert list(params)[:2] == ["action", "id"]
    assert params["full"].default is False
    assert broker.artifacts.__name__ == "artifacts"
    params = inspect.signature(broker.image_gen).parameters
    assert list(params)[:2] == ["action", "prompt"]
    assert broker.image_gen.__name__ == "image_gen"


def test_the_docstrings_teach_the_card_syntax():
    for fn in (broker.artifacts, broker.image_gen):
        doc = fn.__doc__
        assert len([line for line in doc.splitlines() if line.strip()]) <= 10
        assert "[[artifact:" in doc
