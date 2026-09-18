"""The ONE place an image is generated (docs/design/23).

`POST /api/artifacts/generate` is a thin skin over `generate` below, and
`generate` is the whole of the platform's contact with a paid image model:
the request is checked against the registry before a cent is spent; the
caller's hourly budget and the platform's daily cap are read from the
artifact rows — the same rows the Studio lists, so there is no ledger to
drift from them; the reference images are fetched through the store in the
caller's own scope and handed to the executor as files, so the third party
only ever sees what the caller could already see; the executor runs the
`image_gen` tool with the provider keys that exist nowhere else; and the
first file back becomes an artifact with its cost on it, a card in `#art`,
and an event. The API never holds a key and the model never sees a provider
response — only the artifact.

The executor's failures are user-facing by contract (`run.py` prints a
sentence, never a traceback), so they come back as the 502 body verbatim.
"""
import asyncio
import base64
import json
import logging
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from agentplatform import artifact_store as store
from agentplatform.db import Artifact, SchemaMark, SecretMeta, utcnow
from agentplatform.relay import SYSTEM_AUTHOR, is_agent
from agentplatform.relay_store import (channel_by_name, outbound_for_message,
                                       post_relay_message, publish_relay_message)
from agentplatform.tickets import one_line, participant_label

log = logging.getLogger("image_gen")

TOOL = "image_gen"
ART_CHANNEL = "art"
REFERENCES_MAX = 4
# The most prompt a generation takes, checked before anything is spent. The
# artifact's meta keeps the prompt, and meta is capped at 8 KiB by the store —
# a prompt that blew that cap would be refused AFTER the provider was paid,
# with no row left to record the spend. 2000 characters is more than any
# provider reads and well inside the meta cap with the sidecar beside it.
PROMPT_LIMIT = 2000
# What of the prompt a `#art` card quotes; the rest is the artifact's meta.
CARD_PROMPT_LIMIT = 120
# How long a provider's secret status is trusted before the k8s store is
# asked again: the models list is fetched on every Studio open and the store
# is a synchronous API call per block.
PROVIDER_STATUS_TTL_SECONDS = 60
# The generated name's slice of the prompt, after slugging.
NAME_SLUG_LIMIT = 40
# The executor's own timeout is the manifest's; this much more covers the
# request reaching it and the image coming back.
TIMEOUT_GRACE_SECONDS = 30
# Provider → the secret block that carries its key. `run.py`'s KEY_ENV, spelled
# here because a tool is not a package the API can import; a test holds the
# two in lockstep against the manifest's `infra.secrets`.
PROVIDER_SECRETS = {"openai": "openai-api-key", "gemini": "gemini-api-key",
                    "bfl": "bfl-api-key"}
# A block is usable when its key is set and not known-bad: `unprobed` is what
# a secret set out of band reports, and it is how most keys arrive.
CONFIGURED_STATUSES = ("valid", "unprobed")
# The daily notice's mark name, dated: `schema_marks` is the one table every
# service already agrees is for "did this one-off happen", and a day's notice
# is a one-off.
NOTICE_MARK_PREFIX = "art-budget-notice-"
_EXT_OF = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class ImageGenError(Exception):
    """A generation refused or failed, carrying the HTTP status the route
    answers with: 422 for a request the registry refuses, 404 for a reference
    the caller cannot read, 429 for an agent over its hour, 402 for a platform
    over its day, 502 for an executor that failed or could not be reached,
    503 for a tool that is not installed."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class _Reservations:
    """Money and images promised but not yet on a row.

    A generation is check-then-spend with up to 210 s between the two: every
    request that arrives while one is waiting on the executor reads the same
    rows, passes the same caps, and pays. So the caps are checked under this
    lock against the rows PLUS what is reserved here, and a request reserves
    its price (and, for an agent, its one image) before it lets go of the
    lock; the reservation is released when the row exists or the attempt has
    failed. PROCESS-LOCAL, which is exact only because the API runs as one
    replica (`charts/agent-platform/templates/api.yaml` pins `replicas: 1`): a
    second replica would have its own ledger and the window would reopen
    across them. That is the trade the
    design makes — a budget, not a bank — and the row count is still the record."""

    def __init__(self):
        self.lock = asyncio.Lock()
        self.usd = 0.0
        self.by_owner: dict[str, int] = {}

    def take(self, owner: str, price: float) -> None:
        self.usd += price
        self.by_owner[owner] = self.by_owner.get(owner, 0) + 1

    def release(self, owner: str, price: float) -> None:
        self.usd = max(0.0, self.usd - price)
        left = self.by_owner.get(owner, 0) - 1
        if left > 0:
            self.by_owner[owner] = left
        else:
            self.by_owner.pop(owner, None)


_reserved = _Reservations()
# (monotonic seconds, {provider: configured}) — see PROVIDER_STATUS_TTL_SECONDS.
_provider_cache: tuple[float, dict[str, bool]] | None = None


# --- the registry ------------------------------------------------------------------

def load_models(tool_dir: Path) -> list[dict]:
    """`models.json` beside the tool's `run.py`: the registry both the tool and
    the API read, so a model the Studio offers is one the tool knows."""
    return json.loads((Path(tool_dir) / "models.json").read_text())


def _tool(tool_registry):
    info = tool_registry.get(TOOL)
    if info is None or info.manifest is None:
        raise ImageGenError(503, f"the {TOOL} tool is not installed")
    return info


def default_model(models: list[dict]) -> dict:
    return next((m for m in models if m.get("default")), models[0])


def model_view(entry: dict, *, configured: bool) -> dict:
    return {"id": entry["id"], "provider": entry["provider"], "label": entry["label"],
            "price_usd": float(entry["price_usd"]),
            "sizes": entry.get("sizes"), "aspects": entry.get("aspects"),
            "custom_size": bool(entry.get("custom_size", False)),
            "qualities": entry.get("qualities"), "edits": bool(entry.get("edits")),
            "configured": configured, "default": bool(entry.get("default"))}


async def provider_status(session, secret_store) -> dict[str, bool]:
    """Which providers have a usable key, from `secrets_meta` — a set-but-
    unprobed block reads as usable, a probed-invalid one does not — with the
    store consulted for a block that has no meta row, the way the secrets
    page reads them: the store is the truth for existence, meta for status.

    Cached for PROVIDER_STATUS_TTL_SECONDS: the store is the k8s API, called
    synchronously, and this runs on every models list. The store call itself
    is pushed off the event loop for the same reason — a k8s round trip is
    tens of milliseconds the loop must not spend."""
    global _provider_cache
    if _provider_cache is not None \
            and time.monotonic() - _provider_cache[0] < PROVIDER_STATUS_TTL_SECONDS:
        return dict(_provider_cache[1])
    blocks = list(PROVIDER_SECRETS.values())
    rows = {m.name: m.status for m in (await session.execute(
        select(SecretMeta).where(SecretMeta.name.in_(blocks)))).scalars()}
    out = {}
    for provider, block in PROVIDER_SECRETS.items():
        status = rows.get(block, "missing")
        if status == "missing" and await _exists_off_loop(secret_store, block):
            status = "unprobed"
        out[provider] = status in CONFIGURED_STATUSES
    _provider_cache = (time.monotonic(), dict(out))
    return out


async def _exists_off_loop(secret_store, block: str) -> bool:
    """`secret_store.exists` is a coroutine over a synchronous k8s client, so
    it is awaited on a worker thread's own loop rather than this one."""
    return await asyncio.to_thread(asyncio.run, secret_store.exists(block))


async def models(session, tool_registry, secret_store) -> list[dict]:
    """The registry × the secret status: what the Studio's model select and
    the broker's `models` action show."""
    configured = await provider_status(session, secret_store)
    return [model_view(m, configured=configured.get(m["provider"], False))
            for m in load_models(_tool(tool_registry).dir)]


# --- the request -------------------------------------------------------------------

def _check_request(entry: dict, *, prompt: str, size, aspect, quality,
                   reference_count: int) -> dict:
    """The tool's arguments, refused here for what the registry already says
    is wrong — an unknown size, a reference to a model that cannot edit — so
    the executor is never asked for an image it would refuse. What the tool
    can bridge (an aspect on a sizes model, a custom size where the registry
    allows one) is passed through: the bridging is the tool's, not ours."""
    if not prompt:
        raise ImageGenError(422, "prompt is required")
    if len(prompt) > PROMPT_LIMIT:
        raise ImageGenError(422, f"prompt too long (max {PROMPT_LIMIT})")
    if reference_count and not entry.get("edits"):
        raise ImageGenError(422, f"{entry['id']} does not accept reference images")
    sizes, aspects, qualities = entry.get("sizes"), entry.get("aspects"), entry.get("qualities")
    if size and sizes and not entry.get("custom_size") and size not in sizes:
        raise ImageGenError(422, f"size must be one of {', '.join(sizes)} for {entry['id']}")
    if aspect and aspects and aspect not in aspects:
        raise ImageGenError(422, f"aspect must be one of {', '.join(aspects)} for {entry['id']}")
    if quality and qualities and quality not in qualities:
        raise ImageGenError(422, f"quality must be one of {', '.join(qualities)} "
                                 f"for {entry['id']}")
    if quality and not qualities:
        raise ImageGenError(422, f"{entry['id']} has no quality setting")
    args = {"action": "generate", "model": entry["id"], "prompt": prompt}
    for key, value in (("size", size), ("aspect", aspect), ("quality", quality)):
        if value:
            args[key] = value
    return args


def _model_of(models: list[dict], model_id: str | None) -> dict:
    if not model_id:
        return default_model(models)
    entry = next((m for m in models if m["id"] == model_id), None)
    if entry is None:
        raise ImageGenError(422, f"unknown model {model_id!r}")
    return entry


def _price_of(entry: dict) -> float:
    """The registry's estimate, which is what the cap and the row record. An
    entry without one is a registry bug, and a free-looking image would walk
    past the cap forever — so it is refused before anything is spent."""
    price = entry.get("price_usd")
    if not isinstance(price, (int, float)) or isinstance(price, bool) or price <= 0:
        raise ImageGenError(500, f"registry entry {entry.get('id')} has no price")
    return float(price)


# --- budget and spend ---------------------------------------------------------------

def local_zone(settings):
    """The scheduler's reading of a zone name: a bad one is a warning and UTC,
    never a route that cannot answer."""
    try:
        return ZoneInfo(settings.local_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("unknown local_timezone %r; using UTC", settings.local_timezone)
        return timezone.utc


def day_start(settings, now: datetime | None = None) -> datetime:
    """Local midnight, as a UTC instant the rows compare against."""
    local = (now or utcnow()).astimezone(local_zone(settings))
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def month_start(settings, now: datetime | None = None) -> datetime:
    local = (now or utcnow()).astimezone(local_zone(settings))
    return local.replace(day=1, hour=0, minute=0, second=0,
                         microsecond=0).astimezone(timezone.utc)


def _generated_since(since: datetime, owner: str | None = None):
    """Generated rows, deleted ones included: a deleted image was still paid
    for, and a budget that a delete could reset would not be a budget."""
    stmt = select(Artifact).where(Artifact.source == "generated",
                                  Artifact.created_at >= since)
    return stmt.where(Artifact.owner == owner) if owner else stmt


async def generated_count(session, since: datetime, owner: str | None = None) -> int:
    stmt = _generated_since(since, owner).with_only_columns(func.count(Artifact.id))
    return int((await session.execute(stmt)).scalar_one() or 0)


async def spend_since(session, since: datetime) -> float:
    """The sum of `meta.cost_usd` since `since`. Summed in Python from the
    rows' meta rather than in SQL: the JSON column has no one extraction
    syntax across the two dialects, and a day of generations is dozens of
    rows, not thousands."""
    stmt = _generated_since(since).with_only_columns(Artifact.meta)
    total = 0.0
    for meta in (await session.execute(stmt)).scalars():
        cost = (meta or {}).get("cost_usd")
        if isinstance(cost, (int, float)):
            total += float(cost)
    return total


async def spend_stats(session, settings) -> dict:
    """The four spend fields `GET /api/artifacts/stats` carries."""
    now = utcnow()
    month = month_start(settings, now)
    return {"generated_this_month": await generated_count(session, month),
            "spend_this_month_usd": round(await spend_since(session, month), 4),
            "spend_today_usd": round(await spend_since(session, day_start(settings, now)), 4),
            "daily_cap_usd": float(settings.image_gen_daily_usd)}


async def _check_budget(session, owner: str, settings, reserved: int = 0) -> None:
    """The hourly cap, agents only, counting `reserved` images in flight. The
    wait quoted is until the OLDEST row in the window ages out — the moment
    one more will fit — so an agent that respects the answer retries once
    instead of every minute."""
    if not is_agent(owner):
        return
    now = utcnow()
    since = now - timedelta(hours=1)
    cap = settings.image_gen_agent_per_hour
    used = await generated_count(session, since, owner) + reserved
    if used < cap:
        return
    oldest = (await session.execute(
        _generated_since(since, owner).with_only_columns(func.min(Artifact.created_at))
    )).scalar_one()
    if oldest is not None and oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=timezone.utc)
    wait = (oldest + timedelta(hours=1) - now) if oldest is not None else timedelta(hours=1)
    minutes = max(1, math.ceil(wait.total_seconds() / 60))
    raise ImageGenError(429, f"image budget: {used}/{cap} this hour; "
                             f"try again in {minutes} min")


def today_key(settings, now: datetime | None = None) -> str:
    return (now or utcnow()).astimezone(local_zone(settings)).strftime("%Y-%m-%d")


async def _mark_taken(session, mark: str) -> bool:
    return await session.get(SchemaMark, mark) is not None


async def _check_daily_cap(session, producer, settings, reserved: float = 0.0) -> None:
    """The platform's day, for everyone, counting `reserved` dollars in
    flight. Over it, `#art` is told ONCE for the day — the Tickets
    budget-notice pattern: an agent over a cap is an agent retrying, and a
    notice per refusal would be the flood the cap exists to stop. The day is
    marked in `schema_marks` so the notice survives the room being cleared
    and needs no text match to find itself; the mark's key is what makes the
    "once" hold across processes, so losing the insert to another writer is
    the notice having been said, not an error."""
    cap = float(settings.image_gen_daily_usd)
    now = utcnow()
    spent = await spend_since(session, day_start(settings, now)) + reserved
    if spent < cap:
        return
    message = f"daily image spend cap reached: ${spent:.2f} of ${cap:.2f}"
    mark = NOTICE_MARK_PREFIX + today_key(settings, now)
    if not await _mark_taken(session, mark):
        conv = await channel_by_name(session, ART_CHANNEL)
        msg = None
        try:
            session.add(SchemaMark(name=mark))
            if conv is not None:
                msg = await post_relay_message(session, conv, author=SYSTEM_AUTHOR,
                                               body=f"⏸️ paused: {message}; the cap turns "
                                                    f"over at midnight", kind="system")
            await session.commit()
        except IntegrityError:
            # Another writer marked the day first: the notice is theirs.
            await session.rollback()
            msg = None
        if msg is not None:
            await publish_relay_message(producer, conv, msg,
                                        outbound=await outbound_for_message(session, conv, msg))
    raise ImageGenError(402, message)


# --- references ------------------------------------------------------------------

async def _references(session, ids: list[str]) -> tuple[list[dict], list[str]]:
    """The reference artifacts as the executor's `files_in`, fetched through
    the store exactly as the content route would serve them to this caller:
    a deleted or unknown one is 404, a non-image is refused, at most four.
    Named `ref1.<ext>`… because the tool reads references by filename and a
    caller's own name for the artifact is not a filename."""
    ids = list(dict.fromkeys(ids or []))
    if len(ids) > REFERENCES_MAX:
        raise ImageGenError(422, f"at most {REFERENCES_MAX} reference images")
    files, names = [], []
    for i, artifact_id in enumerate(ids, 1):
        row = await store.get(session, artifact_id)
        if row is None:
            raise ImageGenError(404, f"unknown reference artifact {artifact_id}")
        if row.kind != "image":
            raise ImageGenError(422, f"reference {artifact_id} is not an image")
        data = await store.content(session, artifact_id)
        if data is None:
            raise ImageGenError(404, f"unknown reference artifact {artifact_id}")
        name = f"ref{i}.{_EXT_OF.get(row.mime, 'bin')}"
        files.append({"name": name, "mime": row.mime, "b64": base64.b64encode(data).decode()})
        names.append(name)
    return files, names


# --- the executor ---------------------------------------------------------------

async def _run_tool(settings, manifest, *, args: dict, files_in: list[dict],
                    caller: dict) -> tuple[dict, list[str]]:
    """One `/run` on the executor: (the first file, the executor's warnings).
    Its answer is either the tool's file or the tool's own sentence about why
    not; anything else — a refused body, a connection that never came back —
    is the generator being unreachable. The warnings are the executor saying
    what it dropped (an over-cap second file), which belongs on the row."""
    timeout = manifest.timeout_seconds + TIMEOUT_GRACE_SECONDS
    body = {"tool": TOOL, "args": args, "files_in": files_in, "caller": caller}
    try:
        async with httpx.AsyncClient(base_url=settings.executor_url, timeout=timeout) as c:
            r = await c.post("/run", json=body)
    except httpx.HTTPError:
        log.warning("image_gen executor call failed", exc_info=True)
        raise ImageGenError(502, "image generator unreachable")
    if r.status_code != 200:
        raise ImageGenError(502, f"image generator refused the request ({r.status_code})")
    try:
        result = r.json()
    except ValueError:
        raise ImageGenError(502, "image generator returned no image")
    if not result.get("ok"):
        raise ImageGenError(502, str(result.get("error") or "image generation failed"))
    files = result.get("files") or []
    if not files:
        raise ImageGenError(502, "image generator returned no image")
    warnings = [str(w) for w in (result.get("warnings") or [])]
    for warning in warnings:
        log.warning("image_gen: %s", warning)
    return files[0], warnings


# --- the generation -------------------------------------------------------------

def _slug(text: str, limit: int) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")[:limit].strip("-")


def _name_for(entry: dict, prompt: str, filename: str, given) -> str:
    if given:
        return given
    ext = Path(filename).suffix.lstrip(".") or "png"
    slug = _slug(prompt, NAME_SLUG_LIMIT)
    return f"{entry['id']}-{slug}.{ext}" if slug else f"{entry['id']}.{ext}"


def card_body(artifact_id: str, owner: str, model: str, prompt: str) -> str:
    """The `#art` card's text: the chip, then one flattened line. `one_line`
    is where the prompt — somebody else's words — loses its newlines, its
    room mentions and everything past the cap, so the platform's sentence is
    the platform's."""
    return (f"[[artifact:{artifact_id}]]\n"
            f"by {participant_label(owner)} · {model} · \"{one_line(prompt, CARD_PROMPT_LIMIT)}\"")


def card_for(artifact_id: str, owner: str, model: str, prompt: str) -> dict:
    """The `card` payload beside the body, the ticket and wiki cards' shape:
    flat, small, the prompt already flattened. The client renders the body
    through the card branch; a bridge that cannot falls back to the text."""
    return {"type": "artifact", "artifact_id": artifact_id, "owner": owner,
            "model": model, "prompt": one_line(prompt, CARD_PROMPT_LIMIT)}


async def _say_card(session, producer, artifact, prompt: str) -> None:
    """The card, after the artifact is committed: the room is the audience,
    the row is the record, and a missing room — or a room that cannot be
    written to — costs the audience only, never the response.

    An EVENT row, not text, and that is load-bearing: the router re-parses
    `@mentions` from the body of every text row (`relay_router._targets`), so
    a prompt reading "@news retract that" would summon news with a fresh hop
    budget the moment its card landed. Event rows are never read for
    mentions — the ticket card's protection, borrowed whole."""
    try:
        conv = await channel_by_name(session, ART_CHANNEL)
        if conv is None:
            log.warning("no live #%s channel; artifact %s got no card", ART_CHANNEL,
                        artifact.id)
            return
        model = artifact.meta.get("model", "")
        msg = await post_relay_message(
            session, conv, author=SYSTEM_AUTHOR, kind="event", mentions=[],
            body=card_body(artifact.id, artifact.owner, model, prompt),
            card=card_for(artifact.id, artifact.owner, model, prompt))
        await session.commit()
        await publish_relay_message(producer, conv, msg,
                                    outbound=await outbound_for_message(session, conv, msg))
    except Exception:
        log.warning("artifact %s got no #%s card", artifact.id, ART_CHANNEL, exc_info=True)


async def generate(session, *, settings, tool_registry, producer, owner: str,
                   agent: str | None, run_id: str | None, prompt, model=None, size=None,
                   aspect=None, quality=None, seed=None, reference_ids=None, name=None,
                   tags=None) -> Artifact:
    """One image, for `owner` — a participant the CALLER resolved from its
    token. The order is the design's: the request against the registry, the
    budget, the cap, the references, the executor, the artifact, the card,
    and the event `artifact_store.create` publishes.

    Everything that can refuse for the request's own sake — the model, the
    prompt's length, the name and the tags the store would refuse — refuses
    BEFORE the executor is called: after it, the provider has been paid, and
    a refusal then would be money with no row to show for it."""
    info = _tool(tool_registry)
    registry = load_models(info.dir)
    entry = _model_of(registry, model)
    price = _price_of(entry)
    prompt = str(prompt or "").strip()
    reference_ids = [r for r in dict.fromkeys(reference_ids or [])]
    args = _check_request(entry, prompt=prompt, size=size, aspect=aspect, quality=quality,
                          reference_count=len(reference_ids))
    if seed is not None:
        args["seed"] = int(seed)
    store.clean_tags(tags)
    async with _reserved.lock:
        await _check_budget(session, owner, settings,
                            reserved=_reserved.by_owner.get(owner, 0))
        await _check_daily_cap(session, producer, settings, reserved=_reserved.usd)
        _reserved.take(owner, price)
    try:
        files_in, ref_names = await _references(session, reference_ids)
        args["references"] = ref_names
        file, warnings = await _run_tool(settings, info.manifest, args=args,
                                         files_in=files_in,
                                         caller={"agent": agent or "", "run_id": run_id or ""})
        return await _keep(session, producer, settings, file, warnings, entry=entry,
                           owner=owner, run_id=run_id, prompt=prompt,
                           reference_ids=reference_ids, name=name, tags=tags)
    finally:
        _reserved.release(owner, price)


async def _keep(session, producer, settings, file: dict, warnings: list[str], *, entry: dict,
                owner: str, run_id: str | None, prompt: str, reference_ids: list[str],
                name, tags) -> Artifact:
    """The paid image, made a row. From here on a failure is money already
    spent, so it is logged with everything the bill will need — who, which
    model, how much — and answered as a 500 that says so, never a quiet 4xx."""
    sidecar = file.get("meta") if isinstance(file.get("meta"), dict) else {}
    # The sidecar is the record of what ran, minus its echo of the prompt
    # (kept once, at the top). The registry's estimate stands in for a cost
    # it did not report, so the cap never counts a free image.
    params = {k: v for k, v in (sidecar.get("params") or {}).items() if k != "prompt"}
    meta = {**sidecar, "prompt": prompt, "params": params, "reference_ids": reference_ids,
            "tool": TOOL}
    meta.setdefault("model", entry["id"])
    meta.setdefault("provider", entry["provider"])
    if not isinstance(meta.get("cost_usd"), (int, float)):
        meta["cost_usd"] = float(entry["price_usd"])
    if warnings:
        meta["executor_warnings"] = warnings
    try:
        data = base64.b64decode(file.get("b64") or "", validate=True)
        if not data:
            raise ValueError("empty file")
        row = await store.create(session, data=data, owner=owner,
                                 name=_name_for(entry, prompt, file.get("name") or "", name),
                                 claimed_mime=file.get("mime"), source="generated",
                                 meta=meta, tags=tags, run_id=run_id, producer=producer,
                                 settings=settings, allow_generated=True)
    except Exception:
        log.error("image generated but could not be stored: owner=%s model=%s cost_usd=%s",
                  owner, meta["model"], meta["cost_usd"], exc_info=True)
        raise ImageGenError(500, "image generated but could not be stored")
    await _say_card(session, producer, row, prompt)
    return row

