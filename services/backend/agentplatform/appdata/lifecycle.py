"""The App lifecycle (design 39, "Apps as state", "The authority model",
"Tools" → `apps`).

An App is rows: its identity in `app_data_apps`, its definitions in
`app_data_definitions`. The builder works in drafts, one per definition name,
and publishes them as a bundle:

- **Versions.** A publish bumps the App's `approved_version` and writes a row
  only for the definitions it changed, numbered with the new version; a
  removal writes a tombstone. The approved state at version N is the newest
  row at or below N for each name (`records.state_at`), so a rollback is just
  "the state at N, published again as a new version".
- **Validate** checks the whole App the publish would produce (approved state
  overlaid with the drafts): the definition language (A2), the records already
  stored under it, the drafts' bases (`stale_base`), and the authority delta
  (A3) after new fields are settled private.
- **Publish** is a compare-and-swap on `approved_version`. It refuses
  anything inconsistent, and anything that widens authority or drops stored
  data: those are proposals (R1b), so the refusal carries the widening and
  suggests `propose`.
- **Build ops.** Every write takes a `request_id`, stored with a hash of the
  arguments in the same transaction as the write. A retry with the same hash
  gets the stored receipt, refusals included; the same id with other
  arguments is refused rather than answered with another call's result.
  Record writes from the `app_data` tool use the same table.

Everything here works on the platform's async session, like the records
engine. A public write commits once, at the end, with its build op.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError

from agentplatform.appdata import quotas
from agentplatform.appdata import authority as A
from agentplatform.appdata import records as rec
from agentplatform.appdata.access import Caller, RecordError
from agentplatform.appdata.definitions import (
    BUNDLE_KEYS, NAME_RE, SYSTEM_FIELDS, AppBundle, CollectionDef, DefinitionError, PageDef,
    RefField, TableBlock, DetailBlock, MetricBlock, TextBlock, ToolViewDef, UniqueRule,
    _fits_field,
    index_columns, validate_app, validate_definition)
from agentplatform.appdata.models import (AppDataApp, AppDataBuildOp, AppDataDefinition,
                                          AppDataRecord)
from agentplatform.appdata.views import check_view_access, execute_view
from agentplatform.db import utcnow

KINDS = ("collection", "view", "page", "tool")
APP_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
PRINCIPAL_RE = re.compile(r"^(kyle|agent:[a-z0-9][a-z0-9-]{0,62})$")
NOTES_MAX_BYTES = 4096
DESCRIPTION_MAX = 500
REASON_MAX = 2000
REQUEST_ID_MAX = 128
RECENT_OPS = 20
# Builder history: what `get` lists. Record writes share the table, not the list.
BUILD_ACTIONS = ("create", "draft", "notes", "publish", "rollback", "retire",
                 "propose", "proposal_approve", "proposal_decline", "proposal_withdraw")
SAMPLES_PER_COLLECTION = 200
SAMPLE_IDS = 5
SCAN_CHUNK = 1000
# The record checks behind an App's health scan whole collections (a GROUP BY
# per unique rule, a count per required field): seconds on a million records.
# Writes enforce the rules and publish checks stored records against new
# ones, so a violation can only come from a write racing a publish or a row
# written around the engine. `apps list` and `get` reuse a result this
# young for the same approved version; the `health` action checks afresh.
HEALTH_TTL = timedelta(hours=1)
# app id -> ((approved_version, authority_generation), checked_at, violations)
_record_checks: dict[str, tuple[tuple, datetime, list]] = {}
QUOTA_WARN = 0.9

_UNSET: Any = object()


class LifecycleError(RecordError):
    """A refused lifecycle call; same shape as a records refusal so the API
    answers both alike."""


def _refuse(code: str, message: str, status: int = 409, detail: Any = None):
    return LifecycleError(code, message, status, detail)


@dataclass(frozen=True)
class Actor:
    """Who is calling: `kyle` (his browser session) or `agent:<name>` (a run),
    with the run for attribution. `via_tool` is `tool:<name>` when an agent
    acts through that tool's call credential (design 39)."""
    principal: str
    run_id: str | None = None
    via_tool: str | None = None

    def __post_init__(self):
        if not PRINCIPAL_RE.fullmatch(self.principal):
            raise ValueError(f"not a lifecycle principal: {self.principal!r}")

    @property
    def caller(self) -> Caller:
        return Caller(self.principal, via_tool=self.via_tool)


def display(principal: str | None) -> str:
    """The web contract names agents bare (`pai`) and Kyle as `kyle`."""
    return (principal or "").removeprefix("agent:")


def _ts(value) -> str | None:
    return rec.format_datetime(value) if value is not None else None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


# --- the App a call works on ------------------------------------------------------------

async def _app(session, ref: str) -> AppDataApp:
    """An App by id, or failing that by name: agents know their Apps by name."""
    app = await session.get(AppDataApp, ref) if isinstance(ref, str) and ref else None
    if app is None and isinstance(ref, str):
        app = (await session.execute(select(AppDataApp).where(AppDataApp.name == ref))
               ).scalar_one_or_none()
    if app is None:
        raise _refuse("AL-NO-APP", f"no App {ref}", 404)
    return app


def _require_owner(app: AppDataApp, actor: Actor) -> None:
    if rec.owner_principal(app) != actor.principal:
        raise _refuse("AL-NOT-OWNER", f"{actor.principal} doesn't own App {app.name}; "
                      "builders act only on Apps they own", 403)


def _require_reader(app: AppDataApp, actor: Actor) -> None:
    """Builder reads: the owner, or Kyle, who sees every App."""
    if actor.principal != "kyle":
        _require_owner(app, actor)


def _require_active(app: AppDataApp) -> None:
    if app.status != "active":
        raise _refuse("AL-APP-RETIRED", f"App {app.name} is retired", 409)


async def _rows(session, app_id: str) -> list[AppDataDefinition]:
    return list((await session.execute(
        select(AppDataDefinition).where(AppDataDefinition.app_id == app_id)
    )).scalars().all())


def _approved(rows, app: AppDataApp) -> dict[tuple[str, str], AppDataDefinition]:
    return rec.state_at(rows, app.approved_version)


def _drafts(rows) -> dict[tuple[str, str], AppDataDefinition]:
    return {(r.kind, r.name): r for r in rows if r.state == "draft"}


def _bodies(state: dict) -> dict[tuple[str, str], dict]:
    return {key: row.body for key, row in state.items()}


def _doc(bodies: dict[tuple[str, str], dict]) -> tuple[dict, dict]:
    """The bundle document A2 validates, plus where each definition sits in it,
    so an issue's JSON path can name the definition it's about."""
    doc: dict[str, list] = {BUNDLE_KEYS[kind]: [] for kind in KINDS}
    where: dict[tuple[str, int], tuple[str, str]] = {}
    order = {kind: i for i, kind in enumerate(KINDS)}
    for kind, name in sorted(bodies, key=lambda k: (order[k[0]], k[1])):
        key = BUNDLE_KEYS[kind]
        where[(key, len(doc[key]))] = (kind, name)
        doc[key].append(bodies[(kind, name)])
    return doc, where


_PATH = re.compile(rf"^\$\.({'|'.join(BUNDLE_KEYS[k] for k in KINDS)})\[(\d+)\]")


def _issues(exc: DefinitionError, where: dict) -> list[dict]:
    out = []
    for item in exc.issues:
        entry = item.as_dict()
        m = _PATH.match(item.path)
        owner = where.get((m.group(1), int(m.group(2)))) if m else None
        entry["definition"] = {"kind": owner[0], "name": owner[1]} if owner else None
        out.append(entry)
    return out


def _settle(doc: dict, approved_doc: dict) -> dict:
    """A3's settle, plus one repair: a verb whose default holds neither owner
    nor Kyle settles to an empty list, which the language can't say. Kyle is
    the narrowest principal it can (he writes only through templates, which
    are always proposals)."""
    settled = A.settle_new_fields(doc, approved_doc)
    for c in settled.get("collections", []):
        for spec in (c.get("fields") or {}).values():
            access = spec.get("access") if isinstance(spec, dict) else None
            if isinstance(access, dict):
                for verb, names in access.items():
                    if names == []:
                        access[verb] = ["kyle"]
    return settled


# --- build ops --------------------------------------------------------------------------

def _canon(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str)


def args_hash(op: str, app: str | None, args: dict) -> str:
    return hashlib.sha256(_canon({"op": op, "app": app, "args": args}).encode()).hexdigest()


def _check_request_id(request_id: Any) -> None:
    if not isinstance(request_id, str) or not request_id.strip() \
            or len(request_id) > REQUEST_ID_MAX:
        raise _refuse("AL-REQUEST-ID", f"every write takes a request_id (1-{REQUEST_ID_MAX} "
                      "characters); reuse it only to retry the same call", 422)


async def _replay(session, actor: Actor, request_id: str, digest: str):
    row = (await session.execute(select(AppDataBuildOp).where(
        AppDataBuildOp.principal == actor.principal,
        AppDataBuildOp.request_id == request_id))).scalar_one_or_none()
    if row is None:
        return None
    if row.args_hash != digest:
        raise _refuse("AL-REQUEST-REUSED", f"request_id {request_id} was used for a "
                      "different call; use a new request_id for a new call", 409,
                      {"op": row.op})
    receipt = row.receipt or {}
    if receipt.get("status") == "refused":
        error = receipt.get("error") or {}
        raise LifecycleError(error.get("code", "AL-REFUSED"), error.get("message", ""),
                             receipt.get("http_status", 409), error.get("detail"))
    return {**receipt.get("result", {}), "replayed": True}


Finish = Callable[[dict, str], Awaitable[dict]]

# Refusals that describe the moment, not the call: a quota (its window turns,
# usage falls, Kyle raises the limit) or a race (a publish moved the
# definitions, a concurrent write won). Storing one as the receipt would
# refuse every retry of the request_id forever.
RETRYABLE_CODES = frozenset({"AD-DEFINITIONS-MOVED", "AL-CONFLICT"})


def retryable(exc: RecordError) -> bool:
    return exc.code.startswith("AD-QUOTA-") or exc.code in RETRYABLE_CODES


async def _build_op(session, actor: Actor, *, request_id: str, op: str, app_id: str | None,
                    args: dict, work: Callable[[Finish], Awaitable[dict]]) -> dict:
    """Run `work` once per (principal, request_id).

    `work` does the write without committing and ends by awaiting
    `finish(result, summary)`, which stores the receipt and commits both
    together; it can do so inside a lock it holds. A refusal rolls the write
    back and is stored as the receipt, so a retry is refused the same way,
    unless it is one a retry can outgrow (a quota window, a concurrent
    publish or write): that rolls back with no receipt, so the same
    request_id can succeed later."""
    _check_request_id(request_id)
    # A call through a tool is a different call from the same args sent
    # directly: it is checked against the tool's writers, so it never replays
    # the other's receipt.
    digest = args_hash(op, app_id, args if actor.via_tool is None
                       else {**args, "via": actor.via_tool})
    replayed = await _replay(session, actor, request_id, digest)
    if replayed is not None:
        return replayed

    def receipt_row(receipt: dict, target: str | None) -> AppDataBuildOp:
        return AppDataBuildOp(principal=actor.principal, request_id=request_id,
                              app_id=target, op=op, args_hash=digest,
                              receipt=json.loads(_canon(receipt)), run_id=actor.run_id)

    async def finish(result: dict, summary: str) -> dict:
        session.add(receipt_row({"status": "succeeded", "summary": summary,
                                 "result": result}, result.get("app_id", app_id)))
        # Charge the record writes' quota usage with the receipt.
        await quotas.settle(session)
        await session.commit()
        return result

    try:
        return await work(finish)
    except RecordError as exc:
        await session.rollback()
        if retryable(exc):
            raise
        session.add(receipt_row({"status": "refused", "summary": exc.message,
                                 "http_status": exc.status, "error": exc.as_dict()}, app_id))
        try:
            await session.commit()
        except IntegrityError:
            # A concurrent call with this request_id stored its receipt first.
            await session.rollback()
            replayed = await _replay(session, actor, request_id, digest)
            if replayed is not None:
                return replayed
        raise
    except IntegrityError:
        await session.rollback()
        replayed = await _replay(session, actor, request_id, digest)
        if replayed is not None:
            return replayed
        raise _refuse("AL-CONFLICT", "a concurrent write got there first; read again "
                      "and retry", 409)
    except BaseException:
        await session.rollback()
        raise


# --- create, list, get ------------------------------------------------------------------

async def create(session, actor: Actor, *, request_id: str, name: str,
                 timezone: str = "UTC", description: str = "") -> dict:
    """A new App owned by the caller, with the narrowest approved state: no
    definitions at all, so its first publish is checked against nothing."""
    args = {"name": name, "timezone": timezone, "description": description}

    async def work(finish):
        if not isinstance(name, str) or not APP_NAME_RE.fullmatch(name):
            raise _refuse("AL-NAME", "an App name is lowercase letters, digits, `-` and "
                          "`_`, starting with a letter (at most 48 characters)", 422,
                          {"name": name})
        try:
            ZoneInfo(timezone)
        except Exception:
            raise _refuse("AL-TIMEZONE", f"unknown timezone {timezone!r}; use an IANA "
                          "name like Europe/London", 422) from None
        if not isinstance(description, str) or len(description) > DESCRIPTION_MAX:
            raise _refuse("AL-DESCRIPTION", f"a description is at most {DESCRIPTION_MAX} "
                          "characters", 422)
        taken = (await session.execute(select(AppDataApp.id).where(AppDataApp.name == name))
                 ).scalar_one_or_none()
        if taken is not None:
            raise _refuse("AL-NAME-TAKEN", f"the name {name} belongs to another App, "
                          "retired or not; names are never reused", 409)
        kind, owner_id = ("kyle", "kyle") if actor.principal == "kyle" \
            else ("agent", actor.principal.removeprefix("agent:"))
        # Holds the owner's quota row to the commit, so two creates can't
        # both take the last slot.
        await quotas.check_new_app(session, actor.principal)
        app = AppDataApp(name=name, owner_kind=kind, owner_id=owner_id, timezone=timezone,
                         description=description, status="active", notes="",
                         notes_revision=0, authority_generation=0)
        session.add(app)
        await session.flush()
        return await finish({"app_id": app.id, "name": name, "owner": actor.principal,
                             "approved_version": None}, f"Created App {name}.")

    return await _build_op(session, actor, request_id=request_id, op="create", app_id=None,
                           args=args, work=work)


async def _readable_by(session, app: AppDataApp, principal: str) -> bool:
    if rec.owner_principal(app) == principal or principal == "kyle":
        return True
    doc, _ = _doc(_bodies(_approved(await _rows(session, app.id), app)))
    return any(f[0] == "access" and f[1] == principal and f[4] == "read"
               for f in A.compute_facts(doc))


def _summary(app: AppDataApp, health: dict) -> dict:
    return {"id": app.id, "name": app.name, "owner": app.owner_id, "status": app.status,
            "description": app.description or "", "approved_version": app.approved_version,
            "updated_at": _ts(app.updated_at),
            "health": {k: health[k] for k in ("status", "issues", "checked_at")}}


async def list_apps(session, actor: Actor) -> list[dict]:
    """Apps the caller owns or its approved facts let it read; Kyle sees all,
    retired included."""
    apps = (await session.execute(select(AppDataApp).order_by(AppDataApp.name))
            ).scalars().all()
    out = []
    for app in apps:
        if await _readable_by(session, app, actor.principal):
            health = ({"status": "ok", "issues": 0, "checked_at": None}
                      if actor.principal == "login:qa"
                      else await _health(session, app, reuse=True))
            out.append(_summary(app, health))
    return out


async def get_readable_app(session, caller: Caller, app_ref: str) -> dict:
    """QA's narrow builder shell: page names, not drafts, source definitions,
    build notes, health internals or operations. Page/view reads apply the
    collection and per-field facts separately on every request."""
    app = await _app(session, app_ref)
    if caller.principal != "login:qa" or not await _readable_by(session, app,
                                                                   caller.principal):
        raise _refuse("AL-NOT-READER", "this App is not shared with the QA login", 403)
    ctx = await rec.load_app(session, app.id)
    pages = [{"kind": "page", "name": name, "version": page_version(
        await _rows(session, app.id), app, name), "published_at": "",
        "published_by": "", "definition": {"page": name, "title": page.title}}
        for name, page in sorted(ctx.bundle.pages.items())
        if can_read_page(ctx, caller, page)]
    return {**_summary(app, {"status": "ok", "issues": 0, "checked_at": None}),
            "read_only": True, "approved": pages, "drafts": [], "build_notes": None,
            "build_ops": [],
            "health": {"status": "ok", "issues": 0, "checked_at": None,
                       "invalid_bindings": [], "rule_violations": [],
                       "quota": {"records": 0, "records_limit": 0,
                                 "bytes": 0, "bytes_limit": 0}}}


def _draft_view(row: AppDataDefinition) -> dict:
    return {"kind": row.kind, "name": row.name, "revision": row.revision,
            "base_version": row.base_version, "updated_at": _ts(row.updated_at),
            "updated_by": display(row.author),
            "definition": {"removed": True} if row.removed else row.body}


def _op_view(row: AppDataBuildOp) -> dict:
    receipt = row.receipt or {}
    return {"request_id": row.request_id, "action": row.op,
            "status": receipt.get("status", "failed"), "actor": display(row.principal),
            "created_at": _ts(row.created_at), "summary": receipt.get("summary", "")}


async def get_app(session, actor: Actor, app_ref: str) -> dict:
    """One App as the builder area shows it (`StateAppDetail` in
    services/web/src/lib/appData.ts)."""
    app = await _app(session, app_ref)
    _require_reader(app, actor)
    rows = await _rows(session, app.id)
    order = {kind: i for i, kind in enumerate(KINDS)}
    approved = sorted(_approved(rows, app).values(),
                      key=lambda r: (order[r.kind], r.name))
    drafts = sorted(_drafts(rows).values(), key=lambda r: (order[r.kind], r.name))
    ops = (await session.execute(
        select(AppDataBuildOp).where(AppDataBuildOp.app_id == app.id,
                                     AppDataBuildOp.op.in_(BUILD_ACTIONS))
        .order_by(AppDataBuildOp.created_at.desc()).limit(RECENT_OPS))).scalars().all()
    notes = None
    if app.notes_revision:
        notes = {"text": app.notes, "revision": app.notes_revision,
                 "updated_at": _ts(app.notes_updated_at),
                 "updated_by": display(app.notes_updated_by)}
    out = _summary(app, {"status": "ok", "issues": 0, "checked_at": None})
    out.update({
        "approved": [{"kind": r.kind, "name": r.name, "version": r.version,
                      "published_at": _ts(r.updated_at), "published_by": display(r.author),
                      "definition": r.body} for r in approved],
        "drafts": [_draft_view(r) for r in drafts],
        "build_notes": notes,
        "health": await _health(session, app, rows, reuse=True),
        "build_ops": [_op_view(op) for op in ops],
    })
    return out


# --- drafts and notes -------------------------------------------------------------------

async def draft(session, actor: Actor, app_ref: str, *, request_id: str, kind: str,
                definition: dict | None = None, name: str | None = None,
                expected_revision: int | None = None, remove: bool = False,
                discard: bool = False, reason: str = "") -> dict:
    """Save one definition draft under `expected_revision` (None or 0 for a
    name with no draft yet). `remove` drafts dropping a published definition;
    `discard` throws the draft away. The draft records the approved version
    it was written against, which is how validate spots a stale base."""
    app = await _app(session, app_ref)
    _require_owner(app, actor)
    args = {"kind": kind, "definition": definition, "name": name,
            "expected_revision": expected_revision, "remove": remove, "discard": discard,
            "reason": reason}

    async def work(finish):
        _require_active(app)
        if kind not in KINDS:
            raise _refuse("AL-KIND", f"kind is one of {', '.join(KINDS)}", 422,
                          {"kind": kind})
        if remove and discard:
            raise _refuse("AL-ARGS", "remove and discard are separate calls", 422)
        if not isinstance(reason, str) or len(reason) > REASON_MAX:
            raise _refuse("AL-ARGS", f"a reason is at most {REASON_MAX} characters", 422)
        target = name
        if remove or discard:
            if not isinstance(target, str) or not NAME_RE.fullmatch(target):
                raise _refuse("AL-NAME", "name the definition to remove or discard", 422)
        else:
            try:
                validate_definition(kind, definition)
            except DefinitionError as exc:
                raise _refuse("AL-INVALID-DEFINITION", f"the {kind} doesn't validate", 422,
                              {"errors": [i.as_dict() for i in exc.issues]}) from None
            target = definition[kind]
            if name is not None and name != target:
                raise _refuse("AL-NAME-MISMATCH", f"the definition names itself {target}, "
                              f"not {name}", 422)
        row = (await session.execute(select(AppDataDefinition).where(
            AppDataDefinition.app_id == app.id, AppDataDefinition.kind == kind,
            AppDataDefinition.name == target, AppDataDefinition.state == "draft"
        ).with_for_update())).scalar_one_or_none()
        current = row.revision if row is not None else 0
        if (expected_revision or 0) != current:
            raise _refuse("AL-STALE-REVISION", f"the {kind} {target} draft is at revision "
                          f"{current}; read it again before saving", 409,
                          {"revision": current})
        now = utcnow()
        app.updated_at = now
        if discard:
            if row is None:
                raise _refuse("AL-NO-DRAFT", f"no draft of {kind} {target}", 404)
            await session.delete(row)
            await session.flush()
            return await finish({"app_id": app.id, "kind": kind, "name": target,
                                 "discarded": True}, f"Discarded the {kind} {target} draft.")
        if remove and (kind, target) not in _approved(await _rows(session, app.id), app):
            raise _refuse("AL-NOT-PUBLISHED", f"{kind} {target} isn't in the approved state; "
                          "discard its draft instead", 409)
        if row is None:
            # A new open draft counts against the App's limit; editing one
            # already open doesn't.
            await quotas.check_new_draft(session, app.id)
            row = AppDataDefinition(app_id=app.id, kind=kind, name=target, version=0,
                                    state="draft", revision=1)
            session.add(row)
        else:
            row.revision += 1
        row.body = {} if remove else definition
        row.removed = bool(remove)
        row.base_version = app.approved_version
        row.author, row.run_id, row.reason = actor.principal, actor.run_id, reason
        row.updated_at = now
        await session.flush()
        what = f"removal of {kind} {target}" if remove else f"{kind} {target}"
        return await finish({"app_id": app.id, "kind": kind, "name": target,
                             "revision": row.revision, "base_version": row.base_version,
                             "removed": bool(remove)},
                            f"Drafted {what} (revision {row.revision}).")

    return await _build_op(session, actor, request_id=request_id, op="draft",
                           app_id=app.id, args=args, work=work)


async def read_notes(session, actor: Actor, app_ref: str) -> dict:
    app = await _app(session, app_ref)
    _require_reader(app, actor)
    return {"app_id": app.id, "text": app.notes, "revision": app.notes_revision,
            "updated_at": _ts(app.notes_updated_at),
            "updated_by": display(app.notes_updated_by) if app.notes_updated_by else None}


async def write_notes(session, actor: Actor, app_ref: str, *, request_id: str, text: str,
                      expected_revision: int) -> dict:
    """Replace the build notes (the maintainer's runbook, ≤ 4 KB) under a
    compare-and-swap on their revision."""
    app = await _app(session, app_ref)
    _require_owner(app, actor)
    args = {"text": text, "expected_revision": expected_revision}

    async def work(finish):
        _require_active(app)
        if not isinstance(text, str) or len(text.encode()) > NOTES_MAX_BYTES:
            raise _refuse("AL-NOTES-TOO-LONG", f"build notes are at most {NOTES_MAX_BYTES} "
                          "bytes; keep the runbook, link the rest", 422)
        now = utcnow()
        moved = await session.execute(
            update(AppDataApp).where(AppDataApp.id == app.id,
                                     AppDataApp.notes_revision == expected_revision)
            .values(notes=text, notes_revision=AppDataApp.notes_revision + 1,
                    notes_updated_at=now, notes_updated_by=actor.principal, updated_at=now)
            .execution_options(synchronize_session=False))
        if moved.rowcount != 1:
            await session.refresh(app)
            raise _refuse("AL-STALE-REVISION", f"the notes are at revision "
                          f"{app.notes_revision}; read them again before saving", 409,
                          {"revision": app.notes_revision})
        await session.refresh(app)
        return await finish({"app_id": app.id, "revision": app.notes_revision},
                            f"Wrote build notes (revision {app.notes_revision}).")

    return await _build_op(session, actor, request_id=request_id, op="notes",
                           app_id=app.id, args=args, work=work)


# --- validation -------------------------------------------------------------------------

@dataclass
class Assessment:
    """What a candidate approved state would mean, against the current one."""
    errors: list[dict] = dc_field(default_factory=list)
    bundle: AppBundle | None = None
    settled: dict = dc_field(default_factory=dict)
    facts: frozenset = frozenset()
    widening: list[str] = dc_field(default_factory=list)
    delta: dict = dc_field(default_factory=lambda: {"added": [], "removed": []})
    data_dropping: list[str] = dc_field(default_factory=list)
    record_issues: list[dict] = dc_field(default_factory=list)
    reindex: list[str] = dc_field(default_factory=list)
    stale_base: list[dict] = dc_field(default_factory=list)

    @property
    def needs_proposal(self) -> bool:
        return bool(self.widening or self.data_dropping)

    @property
    def publishable(self) -> bool:
        return not (self.errors or self.needs_proposal or self.record_issues
                    or self.stale_base)


async def _assess(session, app: AppDataApp, current: dict, candidate: dict, *,
                  drafts: dict | None = None, rows=None) -> Assessment:
    """Check a candidate approved state (`{(kind, name): body}`) against the
    current one: language, records, bases and authority."""
    out = Assessment()
    doc, where = _doc(candidate)
    current_doc, _ = _doc(current)
    try:
        validate_app(doc)
    except DefinitionError as exc:
        out.errors = _issues(exc, where)
        return out
    out.settled = _settle(doc, current_doc)
    out.bundle = validate_app(out.settled)
    out.facts = A.compute_facts(out.settled)
    old_facts = A.compute_facts(current_doc)
    out.widening = A.widening(old_facts, out.facts)
    out.delta = {"added": A.describe(out.facts - old_facts),
                 "removed": A.describe(old_facts - out.facts)}
    await _check_records(session, app, current, out)
    for key, row in (drafts or {}).items():
        published = [r.version for r in rows or () if r.state == "published"
                     and (r.kind, r.name) == key]
        latest = max(published, default=None)
        if latest is not None and (row.base_version is None or latest > row.base_version):
            out.stale_base.append({"kind": key[0], "name": key[1],
                                   "base_version": row.base_version,
                                   "published_version": latest})
    return out


def _doc_expr(c: CollectionDef, name: str):
    """A field read from `doc` itself, never a side column: checks run before
    a reindex, while the side columns still follow the old `indexed` list."""
    ftype = c.fields[name].type if name in c.fields else "string"
    if ftype in ("int", "number"):
        return AppDataRecord.doc[name].as_float()
    if ftype == "bool":
        return AppDataRecord.doc[name].as_boolean()
    return AppDataRecord.doc[name].as_string()


def _scope(app_id: str, collection: str):
    return and_(AppDataRecord.app_id == app_id, AppDataRecord.collection == collection)


async def _duplicates(session, app_id: str, c: CollectionDef,
                      fields: list[str]) -> tuple[int, list[str]]:
    """Records sharing values on `fields`: the total, and a few of their ids."""
    exprs = [_doc_expr(c, f) for f in fields]
    groups = (await session.execute(
        select(*exprs, func.count()).where(_scope(app_id, c.collection))
        .group_by(*exprs).having(func.count() > 1))).all()
    total = sum(g[-1] for g in groups)
    ids: list[str] = []
    for group in groups:
        if len(ids) >= SAMPLE_IDS:
            break
        conds = [e.is_(None) if v is None else e == v for e, v in zip(exprs, group[:-1])]
        ids += (await session.execute(
            select(AppDataRecord.id).where(_scope(app_id, c.collection), *conds)
            .order_by(AppDataRecord.id).limit(SAMPLE_IDS - len(ids)))).scalars().all()
    return total, ids


async def _missing(session, app_id: str, c: CollectionDef,
                   field: str) -> tuple[int, list[str]]:
    cond = and_(_scope(app_id, c.collection), _doc_expr(c, field).is_(None))
    n = (await session.execute(select(func.count()).select_from(AppDataRecord)
                               .where(cond))).scalar_one()
    ids = (await session.execute(select(AppDataRecord.id).where(cond)
                                 .order_by(AppDataRecord.id).limit(SAMPLE_IDS))
           ).scalars().all() if n else []
    return n, list(ids)


def _shape(spec: dict | None) -> dict | None:
    """What of a field's spec bears on stored values (not access or labels)."""
    if spec is None:
        return None
    return {k: v for k, v in spec.items() if k not in ("access", "label", "description")}


def _unique_fields(c: CollectionDef) -> list[tuple[str, ...]]:
    return [tuple(r.fields) for r in c.rules if isinstance(r, UniqueRule)]


async def _scan(session, app_id: str, collection: str):
    """Every record of a collection, in id order, a chunk at a time."""
    last = None
    while True:
        stmt = select(AppDataRecord).where(_scope(app_id, collection))
        if last is not None:
            stmt = stmt.where(AppDataRecord.id > last)
        chunk = (await session.execute(stmt.order_by(AppDataRecord.id).limit(SCAN_CHUNK))
                 ).scalars().all()
        if not chunk:
            return
        for record in chunk:
            yield record
        last = chunk[-1].id


async def _check_records(session, app: AppDataApp, current: dict, out: Assessment) -> None:
    """The candidate against the records already stored: data a change would
    drop, values the new definitions refuse, new rules existing records break,
    and the collections whose side columns must be rebuilt."""
    counts = dict((await session.execute(
        select(AppDataRecord.collection, func.count()).where(AppDataRecord.app_id == app.id)
        .group_by(AppDataRecord.collection))).all())
    new = out.bundle.collections
    for (kind, name), _ in sorted(current.items()):
        if kind == "collection" and name not in new and counts.get(name):
            out.data_dropping.append(f"collection {name} holds "
                                     f"{_plural(counts[name], 'record')}")
    for name, c in sorted(new.items()):
        if not counts.get(name):
            continue
        old_body = current.get(("collection", name))
        new_body = next(b for b in out.settled["collections"] if b["collection"] == name)
        if old_body == new_body:
            continue
        try:
            old = validate_definition("collection", old_body) if old_body else None
        except DefinitionError:
            # Approved under an older language: nothing to compare against.
            old = None
        old_fields = old_body.get("fields", {}) if old_body else {}
        for field in sorted(set(old_fields) - set(c.fields)):
            n = (await session.execute(select(func.count()).select_from(AppDataRecord).where(
                _scope(app.id, name),
                AppDataRecord.doc[field].as_string().is_not(None)))).scalar_one()
            if n:
                out.data_dropping.append(f"{name}.{field} holds data in "
                                         f"{_plural(n, 'record')}")
        changed = [f for f, spec in c.fields.items()
                   if _shape(new_body["fields"][f]) != _shape(old_fields.get(f))]
        for f in changed:
            spec = c.fields[f]
            if spec.required and not (old_fields.get(f) or {}).get("required"):
                n, ids = await _missing(session, app.id, c, f)
                if n:
                    out.record_issues.append({
                        "code": "AL-RECORDS-REQUIRED", "collection": name, "field": f,
                        "count": n, "record_ids": ids,
                        "message": f"{f} is now required but {_plural(n, 'record')} "
                                   "lack it"})
        reindex = old is None or index_columns(old) != index_columns(c)
        if reindex:
            out.reindex.append(name)
        if changed or reindex:
            await _check_values(session, app, c, changed, reindex, out)
        old_unique = set(_unique_fields(old)) if old else set()
        for fields in _unique_fields(c):
            if fields in old_unique:
                continue
            n, ids = await _duplicates(session, app.id, c, list(fields))
            if n:
                out.record_issues.append({
                    "code": "AL-RECORDS-UNIQUE", "collection": name,
                    "rule": f"unique({', '.join(fields)})", "count": n, "record_ids": ids,
                    "message": f"{_plural(n, 'record')} share values on "
                               f"({', '.join(fields)})"})


async def _check_values(session, app: AppDataApp, c: CollectionDef, changed: list[str],
                        reindex: bool, out: Assessment) -> None:
    bad: dict[tuple[str, str], list[str]] = {}
    refs = {f: c.fields[f].collection for f in changed if isinstance(c.fields[f], RefField)}
    ref_values: dict[str, set] = {f: set() for f in refs}
    async for record in _scan(session, app.id, c.collection):
        for f in changed:
            value = record.doc.get(f)
            if value is None:
                continue
            if not _fits_field(c, f, value, bounds=True):
                bad.setdefault(("AL-RECORDS-INVALID", f), []).append(record.id)
            elif f in refs:
                ref_values[f].add(value)
        if reindex:
            try:
                rec.side_columns(c, record.doc)
            except RecordError as exc:
                field = (exc.detail or {}).get("field", "")
                bad.setdefault(("AL-RECORDS-INDEX", field), []).append(record.id)
    for f, values in ref_values.items():
        found: set = set()
        listed = sorted(values)
        for i in range(0, len(listed), 500):
            found |= set((await session.execute(select(AppDataRecord.id).where(
                _scope(app.id, refs[f]), AppDataRecord.id.in_(listed[i:i + 500])))
            ).scalars().all())
        if values - found:
            out.record_issues.append({
                "code": "AL-RECORDS-REF", "collection": c.collection, "field": f,
                "count": len(values - found), "record_ids": sorted(values - found)[:SAMPLE_IDS],
                "message": f"{f} points at {refs[f]} records that don't exist"})
    messages = {"AL-RECORDS-INVALID": "stored values the new field definition refuses",
                "AL-RECORDS-INDEX": "values too long to index"}
    for (code, f), ids in sorted(bad.items()):
        out.record_issues.append({"code": code, "collection": c.collection, "field": f,
                                  "count": len(ids), "record_ids": ids[:SAMPLE_IDS],
                                  "message": f"{f}: {_plural(len(ids), 'record')} with "
                                             f"{messages[code]}"})


def _selected(drafts: dict, only) -> dict:
    if only is None:
        return drafts
    keys = []
    for item in only:
        if not isinstance(item, dict) or item.get("kind") not in KINDS \
                or not isinstance(item.get("name"), str):
            raise _refuse("AL-ARGS", "`only` lists {kind, name} pairs", 422)
        key = (item["kind"], item["name"])
        if key not in drafts:
            raise _refuse("AL-NO-DRAFT", f"no draft of {key[0]} {key[1]}", 404)
        keys.append(key)
    return {key: drafts[key] for key in keys}


def _overlay(current: dict, drafts: dict) -> dict:
    out = dict(current)
    for key, row in drafts.items():
        if row.removed:
            out.pop(key, None)
        else:
            out[key] = row.body
    return out


def _changes(row: AppDataDefinition, current: dict) -> bool:
    """Whether publishing this draft would change the approved state."""
    key = (row.kind, row.name)
    return key in current if row.removed else current.get(key) != row.body


def _report(app: AppDataApp, a: Assessment, app_moved: bool) -> dict:
    return {"ok": not a.errors, "publishable": a.publishable and not app_moved,
            "approved_version": app.approved_version, "errors": a.errors,
            "record_issues": a.record_issues, "data_dropping": a.data_dropping,
            "widening": a.widening, "delta": a.delta, "stale_base": a.stale_base,
            "app_moved": app_moved,
            "suggest": "propose" if a.needs_proposal else None}


async def validate(session, actor: Actor, app_ref: str, *,
                   expected_approved_version: Any = _UNSET, only=None) -> dict:
    """Validate what publishing the drafts would make the App: stable error
    codes with paths and the definition they belong to, record issues, data a
    change would drop, the authority delta and widening, and `stale_base`.
    Given `expected_approved_version`, `app_moved` says whether the App has
    published since."""
    app = await _app(session, app_ref)
    _require_reader(app, actor)
    rows = await _rows(session, app.id)
    current = _bodies(_approved(rows, app))
    drafts = _selected(_drafts(rows), only)
    a = await _assess(session, app, current, _overlay(current, drafts), drafts=drafts,
                      rows=rows)
    moved = expected_approved_version is not _UNSET and \
        expected_approved_version != app.approved_version
    return _report(app, a, moved)


def _refuse_assessment(app: AppDataApp, a: Assessment) -> None:
    if a.errors:
        raise _refuse("AL-INVALID", "the App doesn't validate", 422, {"errors": a.errors})
    if a.stale_base:
        raise _refuse("AL-STALE-BASE", "a draft was written against an older version of "
                      "its definition; read the approved one and save the draft again",
                      409, {"stale_base": a.stale_base,
                            "approved_version": app.approved_version})
    if a.needs_proposal:
        raise _refuse("AL-NEEDS-PROPOSAL", "this widens the App's approved authority or "
                      "drops stored data, which needs Kyle's approval: propose it", 409,
                      {"widening": a.widening, "data_dropping": a.data_dropping,
                       "delta": a.delta, "suggest": "propose"})
    if a.record_issues:
        raise _refuse("AL-INCONSISTENT", "records already stored don't fit the new "
                      "definitions", 409, {"record_issues": a.record_issues})


async def _swap_version(session, app: AppDataApp, expected: int | None) -> int:
    """Compare-and-swap the approved version, bumping the authority generation
    that keys every cached view result."""
    new = (expected or 0) + 1
    cond = AppDataApp.approved_version.is_(None) if expected is None \
        else AppDataApp.approved_version == expected
    moved = await session.execute(
        update(AppDataApp).where(AppDataApp.id == app.id, cond)
        .values(approved_version=new, updated_at=utcnow(),
                authority_generation=AppDataApp.authority_generation + 1)
        .execution_options(synchronize_session=False))
    if moved.rowcount != 1:
        raise _refuse("AL-STALE-BASE", "the App published since you read it", 409,
                      {"approved_version": app.approved_version})
    await session.refresh(app)
    return new


def _require_base(app: AppDataApp, expected) -> None:
    if expected != app.approved_version:
        raise _refuse("AL-STALE-BASE", f"the App is at approved version "
                      f"{app.approved_version}, not {expected}", 409,
                      {"approved_version": app.approved_version})


def _settled_body(a: Assessment, key: tuple[str, str], body: dict) -> dict:
    if key[0] != "collection":
        return body
    return next(b for b in a.settled["collections"] if b["collection"] == key[1])


async def _reindex(session, app_id: str, a: Assessment) -> None:
    """Rebuild the side columns, flushing and dropping each chunk as it goes
    so a million-record collection never sits in the session at once."""
    for name in a.reindex:
        c = a.bundle.collections[name]
        pending: list[AppDataRecord] = []
        async for record in _scan(session, app_id, name):
            for column, value in rec.side_columns(c, record.doc).items():
                setattr(record, column, value)
            pending.append(record)
            if len(pending) >= SCAN_CHUNK:
                await _flush_out(session, pending)
        await _flush_out(session, pending)


async def _flush_out(session, records: list[AppDataRecord]) -> None:
    await session.flush()
    for record in records:
        session.expunge(record)
    records.clear()


def _published(entries) -> list[dict]:
    order = {kind: i for i, kind in enumerate(KINDS)}
    return sorted(entries, key=lambda e: (order[e["kind"]], e["name"]))


def _summary_line(verb: str, version: int, entries: list[dict]) -> str:
    names = ", ".join(("removed " if e.get("removed") else "") + f"{e['kind']} {e['name']}"
                      for e in entries)
    return f"{verb} version {version} ({names})."


# --- publish, rollback, retire ----------------------------------------------------------

async def _publish_core(session, app: AppDataApp, a: Assessment, *, expected: int | None,
                        changes: dict[tuple[str, str], dict | None], author: str,
                        run_id: str | None, reason: str, drafts: dict | None = None,
                        approved_by: str | None = None,
                        proposal_id: str | None = None) -> tuple[int, list[dict]]:
    """Make `changes` ({(kind, name): body, or None to remove}) the next
    approved version, once `a` has passed: the compare-and-swap, a row per
    change (a draft in `drafts` is published in place, anything else gets a
    new row), the settled bodies and the reindex. Publish, rollback and an
    approved proposal all end here. Doesn't commit."""
    version = await _swap_version(session, app, expected)
    now = utcnow()
    entries = []
    for key, body in changes.items():
        row = (drafts or {}).get(key)
        if row is None:
            row = AppDataDefinition(app_id=app.id, kind=key[0], name=key[1], revision=1,
                                    base_version=expected, reason=reason)
            session.add(row)
        else:
            row.reason = reason or row.reason
        row.state, row.version, row.removed = "published", version, body is None
        row.body = {} if body is None else _settled_body(a, key, body)
        row.author, row.run_id = author, run_id
        row.approved_by, row.proposal_id = approved_by, proposal_id
        row.updated_at = now
        entries.append({"kind": key[0], "name": key[1], "version": version,
                        **({"removed": True} if body is None else {})})
    await session.flush()
    await _reindex(session, app.id, a)
    return version, _published(entries)


def rollback_changes(a: Assessment, current: dict, target: dict) -> dict:
    """What making `target` current changes: each definition whose settled
    body differs, and a removal for each one `target` lacks."""
    out: dict[tuple[str, str], dict | None] = {}
    for key in sorted(set(current) | set(target)):
        if key not in target:
            out[key] = None
        elif current.get(key) != _settled_body(a, key, target[key]):
            out[key] = target[key]
    return out


def _locked_by(current: dict, candidate: dict) -> set[str]:
    """The collections whose stored records a move from `current` to
    `candidate` checks: every collection added, changed or removed, and the
    targets of those collections' refs, whose ids the ref check reads."""
    names = {name for kind, name in set(current) | set(candidate)
             if kind == "collection"
             and current.get((kind, name)) != candidate.get((kind, name))}
    for name in list(names):
        body = candidate.get(("collection", name)) or {}
        fields = body.get("fields") if isinstance(body, dict) else None
        for spec in (fields or {}).values():
            if isinstance(spec, dict) and spec.get("type") == "ref" \
                    and isinstance(spec.get("collection"), str):
                names.add(spec["collection"])
    return names


def _consistency_lock(session, app_id: str, current: dict, candidate: dict):
    """Hold every checked collection's lock exclusive across the record
    checks and the commit: record writes take the same locks, so none lands
    between a check passing and the definitions it passed for going live, and
    one that waited re-reads the definitions after (`records.write_lock`)."""
    return rec.locked_collections(session, app_id, exclusive=_locked_by(current, candidate))


async def publish(session, actor: Actor, app_ref: str, *, request_id: str,
                  expected_approved_version: int | None, only=None,
                  reason: str = "") -> dict:
    """Publish the drafts (or `only` some) as the next approved version, if
    the result is consistent and inside the approved authority. New fields
    are stored settled private."""
    app = await _app(session, app_ref)
    _require_owner(app, actor)
    args = {"expected_approved_version": expected_approved_version, "only": only,
            "reason": reason}

    async def work(finish):
        _require_active(app)
        _require_base(app, expected_approved_version)
        rows = await _rows(session, app.id)
        current_rows = _approved(rows, app)
        current = _bodies(current_rows)
        drafts = _selected(_drafts(rows), only)
        changes = {key: row for key, row in drafts.items() if _changes(row, current)}
        if not changes:
            raise _refuse("AL-NOTHING-TO-PUBLISH", "no draft changes the approved state",
                          409)
        candidate = _overlay(current, changes)
        async with _consistency_lock(session, app.id, current, candidate):
            a = await _assess(session, app, current, candidate, drafts=changes, rows=rows)
            _refuse_assessment(app, a)
            for key, row in drafts.items():
                if key not in changes:
                    await session.delete(row)
            version, entries = await _publish_core(
                session, app, a, expected=expected_approved_version,
                changes={key: None if row.removed else row.body
                         for key, row in changes.items()},
                drafts=changes, author=actor.principal, run_id=actor.run_id, reason=reason)
            return await finish({"app_id": app.id, "approved_version": version,
                                 "authority_generation": app.authority_generation,
                                 "published": entries, "digest": A.digest(a.facts)},
                                _summary_line("Published", version, entries))

    return await _build_op(session, actor, request_id=request_id, op="publish",
                           app_id=app.id, args=args, work=work)


async def rollback(session, actor: Actor, app_ref: str, *, request_id: str, to_version: int,
                   expected_approved_version: int | None, reason: str = "") -> dict:
    """Make an earlier approved state current again, as a new version, under
    publish's checks: a rollback that widens or drops data is a proposal."""
    app = await _app(session, app_ref)
    _require_owner(app, actor)
    args = {"to_version": to_version, "expected_approved_version": expected_approved_version,
            "reason": reason}

    async def work(finish):
        _require_active(app)
        _require_base(app, expected_approved_version)
        if app.approved_version is None or isinstance(to_version, bool) \
                or not isinstance(to_version, int) \
                or not 1 <= to_version < app.approved_version:
            raise _refuse("AL-ROLLBACK-TARGET", "roll back to an earlier approved version, "
                          f"1 to {(app.approved_version or 1) - 1}", 422)
        rows = await _rows(session, app.id)
        current = _bodies(_approved(rows, app))
        target = _bodies(rec.state_at(rows, to_version))
        async with _consistency_lock(session, app.id, current, target):
            a = await _assess(session, app, current, target)
            _refuse_assessment(app, a)
            version, entries = await _publish_core(
                session, app, a, expected=expected_approved_version,
                changes=rollback_changes(a, current, target), author=actor.principal,
                run_id=actor.run_id, reason=reason or f"rollback to version {to_version}")
            return await finish({"app_id": app.id, "approved_version": version,
                                 "rolled_back_to": to_version,
                                 "authority_generation": app.authority_generation,
                                 "published": entries, "digest": A.digest(a.facts)},
                                _summary_line(f"Rolled back to {to_version} as", version,
                                              entries))

    return await _build_op(session, actor, request_id=request_id, op="rollback",
                           app_id=app.id, args=args, work=work)


async def retire(session, actor: Actor, app_ref: str, *, request_id: str,
                 reason: str = "") -> dict:
    """Hide the App and stop its views. Its records, definitions and quota
    use stay; deleting them is Kyle's call, and the name stays taken."""
    app = await _app(session, app_ref)
    _require_owner(app, actor)

    async def work(finish):
        _require_active(app)
        now = utcnow()
        app.status, app.retired_at, app.updated_at = "retired", now, now
        await session.flush()
        return await finish({"app_id": app.id, "status": "retired"},
                            f"Retired App {app.name}" + (f": {reason}" if reason else "."))

    return await _build_op(session, actor, request_id=request_id, op="retire",
                           app_id=app.id, args={"reason": reason}, work=work)


async def transfer_owned_apps(session, agent: str) -> int:
    """An owner agent is being deleted: its Apps pass to Kyle. `owner` in a
    definition means whoever owns the App now, so access moves with it, and
    the generation bump retires cached results computed for the old owner.
    Doesn't commit: it rides the deletion's transaction."""
    app_ids = (await session.execute(
        select(AppDataApp.id).where(AppDataApp.owner_kind == "agent",
                                    AppDataApp.owner_id == agent)
        .order_by(AppDataApp.id))).scalars().all()
    if not app_ids:
        return 0
    await session.execute(
        update(AppDataApp).where(AppDataApp.id.in_(app_ids))
        .values(owner_kind="kyle", owner_id="kyle", updated_at=utcnow(),
                authority_generation=AppDataApp.authority_generation + 1)
        .execution_options(synchronize_session=False))
    # Their storage moves with them, one App at a time in id order; each
    # takes its App's quota row, then the two owners' (quotas' lock order).
    for app_id in app_ids:
        await quotas.transfer_owner(session, app_id, f"agent:{agent}", "kyle")
    return len(app_ids)


# --- authority and health ---------------------------------------------------------------

async def authority(session, actor: Actor, app_ref: str) -> dict:
    """The approved state's authority facts, in plain words."""
    app = await _app(session, app_ref)
    _require_reader(app, actor)
    doc, _ = _doc(_bodies(_approved(await _rows(session, app.id), app)))
    facts = A.compute_facts(doc)
    return {"app_id": app.id, "approved_version": app.approved_version,
            "authority_generation": app.authority_generation, "digest": A.digest(facts),
            "facts": A.describe(facts)}


async def health(session, actor: Actor, app_ref: str) -> dict:
    app = await _app(session, app_ref)
    _require_reader(app, actor)
    return await _health(session, app)


async def _record_violations(session, app: AppDataApp, bundle) -> list[dict]:
    violations = []
    for name, c in sorted(bundle.collections.items()):
        for fields in _unique_fields(c):
            n, ids = await _duplicates(session, app.id, c, list(fields))
            if n:
                violations.append({"collection": name,
                                   "rule": f"unique({', '.join(fields)})",
                                   "count": n, "record_ids": ids})
        for f, spec in c.fields.items():
            if spec.required:
                n, ids = await _missing(session, app.id, c, f)
                if n:
                    violations.append({"collection": name, "rule": f"required({f})",
                                       "count": n, "record_ids": ids})
    return violations


async def _health(session, app: AppDataApp, rows=None, *, reuse: bool = False) -> dict:
    """Approved definitions that no longer validate (checked on every read),
    stored records that break a rule (reused for HEALTH_TTL when `reuse`),
    and the quota use quotas enforce. `checked_at` is when the records were
    checked."""
    rows = rows if rows is not None else await _rows(session, app.id)
    doc, where = _doc(_bodies(_approved(rows, app)))
    invalid, violations = [], []
    now = utcnow()
    checked_at = now
    try:
        bundle = validate_app(doc)
    except DefinitionError as exc:
        bundle = None
        invalid = [{"kind": i["definition"]["kind"] if i["definition"] else "collection",
                    "name": i["definition"]["name"] if i["definition"] else "",
                    "code": i["code"], "message": i["message"]}
                   for i in _issues(exc, where)]
    if bundle is not None:
        key = (app.approved_version, app.authority_generation)
        cached = _record_checks.get(app.id)
        if reuse and cached and cached[0] == key and now - cached[1] < HEALTH_TTL:
            _, checked_at, violations = cached
        else:
            violations = await _record_violations(session, app, bundle)
            _record_checks[app.id] = (key, now, violations)
    usage = await quotas.describe(session, "app", app.id, now=now)
    records, size = usage["used"]["records"], usage["used"]["bytes"]
    records_limit = usage["limits"]["max_records"]
    bytes_limit = usage["limits"]["max_bytes"]
    issues = len(invalid) + len(violations)
    status = "failing" if issues else (
        "warn" if records >= QUOTA_WARN * records_limit or size >= QUOTA_WARN * bytes_limit
        else "ok")
    return {"status": status, "issues": issues, "checked_at": _ts(checked_at),
            "invalid_bindings": invalid, "rule_violations": violations,
            "quota": {"records": int(records), "records_limit": int(records_limit),
                      "bytes": int(size), "bytes_limit": int(bytes_limit)}}


# --- pages for the web ------------------------------------------------------------------

_FORMATS = {"auto": None, "relative_time": "datetime"}


def _binding(value):
    """A block parameter as the web's ParamBinding: a literal string, or a
    value read from the page URL's query string."""
    if isinstance(value, dict):
        return {"query": value["page_param"]}
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _link_fields(bundle, view_name: str) -> set[str]:
    """The `link: true` url fields behind a view: the only ones the web may
    render as outbound anchors."""
    view = bundle.views.get(view_name)
    c = bundle.collections.get(view.collection) if view is not None and not isinstance(
        view, ToolViewDef) else None
    if c is None:
        return set()
    return {n for n, f in c.fields.items() if f.type == "url" and f.link}


def _column(column, links: frozenset | set = frozenset()) -> dict:
    out = {"field": column.field}
    if column.label is not None:
        out["label"] = column.label
    fmt = "link" if column.field in links else _FORMATS.get(column.format, column.format)
    if fmt is not None:
        out["format"] = fmt
    return out


def page_for_web(page: PageDef, bundle, *, with_actions: bool = False) -> dict:
    """A validated page as `PageV2` (services/web/src/lib/appData.ts).

    Only Kyle sees the actionable metadata; dispatch still resolves and checks
    the published template server-side, never trusting this presentation data.
    """
    components = []
    for block in page.blocks:
        if isinstance(block, TextBlock):
            item = {"kind": "text", "style": block.style, "text": block.text}
            if block.link is not None:
                item["link"] = ({"page": block.link.page} if block.link.page is not None
                                else {"path": block.link.path})
        elif isinstance(block, MetricBlock):
            item = {"kind": "metric", "label": block.label, "view": block.view}
        elif isinstance(block, TableBlock):
            item = {"kind": "table", "view": block.view,
                    "columns": [_column(c, _link_fields(bundle, block.view))
                                for c in block.columns]}
            if block.title is not None:
                item["label"] = block.title
            if block.row_link is not None:
                item["row_link"] = {"page": block.row_link.page,
                                    "params": {block.row_link.param: "id"}}
        else:
            links = _link_fields(bundle, block.view)
            item = {"kind": "detail", "view": block.view,
                    "fields": [{"field": f, "format": "link"} if f in links else {"field": f}
                               for f in block.fields]}
            if block.title is not None:
                item["label"] = block.title
        if getattr(block, "params", None):
            item["params"] = {k: _binding(v) for k, v in block.params.items()}
        if with_actions and getattr(block, "actions", None):
            item["actions"] = list(block.actions)
        components.append(item)
    result = {"renderer": "typed/v2", "title": page.title, "components": components}
    if with_actions and page.actions:
        result["actions"] = [{"name": t.name, "kind": t.kind,
                              "label": t.label or t.name.replace("_", " ").title(),
                              "collection": t.collection,
                              "editable_fields": [
                                  {"name": name, **bundle.collections[t.collection].fields[name]
                                   .model_dump(mode="json", include={"type", "label", "required",
                                                                     "min", "max", "values"},
                                               exclude_none=True)}
                                  for name in getattr(t, "editable_fields", [])]}
                             for t in page.actions]
    return result


def page_version(rows, app: AppDataApp, name: str) -> int | None:
    row = _approved(rows, app).get(("page", name))
    return row.version if row is not None else None


def can_read_page(ctx: rec.AppContext, caller: Caller, page: PageDef) -> bool:
    """A page with views is readable by whoever sees the rows of one of them;
    a text-only page by anyone who may read the App."""
    views = [b.view for b in page.blocks if not isinstance(b, TextBlock)]
    if not views:
        return True
    return any(_can_read_view(ctx, caller, ctx.bundle.views[v]) for v in views)


def _can_read_view(ctx: rec.AppContext, caller: Caller, view) -> bool:
    if isinstance(view, ToolViewDef):
        app_tool = ctx.bundle.app_tools.get(view.tool)
        if app_tool is None:
            return False
        return all(ctx.access(ctx.collection(app_tool.roles[role].collection), caller)
                   .can_see_rows() for role in view.sources)
    try:
        check_view_access(ctx, caller, view)
    except RecordError:
        return False
    return True


# --- preview ----------------------------------------------------------------------------

async def preview(session, actor: Actor, app_ref: str, *, kind: str, name: str,
                  params: dict | None = None, as_: str | None = None,
                  samples: dict | None = None, limit: int | None = None,
                  cursor: str | None = None) -> dict:
    """Render a draft page or run a draft view, as the App would be with every
    draft published, without side effects. Unpublished collections read the
    builder's sample records, which are inserted in a transaction that is
    always rolled back; published ones read real data. `as_` renders what
    that principal would see, never more than the builder may."""
    app = await _app(session, app_ref)
    _require_reader(app, actor)
    if kind not in ("view", "page"):
        raise _refuse("AL-KIND", "preview a view or a page", 422)
    viewer = as_ or actor.principal
    if not isinstance(viewer, str) or not PRINCIPAL_RE.fullmatch(viewer):
        raise _refuse("AL-PRINCIPAL", "`as` is kyle or agent:<name>", 422)
    try:
        rows = await _rows(session, app.id)
        approved = _approved(rows, app)
        current = _bodies(approved)
        doc, where = _doc(_overlay(current, _drafts(rows)))
        try:
            validate_app(doc)
        except DefinitionError as exc:
            raise _refuse("AL-INVALID", "the App doesn't validate with its drafts", 422,
                          {"errors": _issues(exc, where)}) from None
        bundle = validate_app(_settle(doc, _doc(current)[0]))
        ctx = rec.AppContext(
            app_id=app.id, owner=rec.owner_principal(app), bundle=bundle,
            timezone=app.timezone or "UTC", status="active", name=app.name,
            versions={n: approved[("collection", n)].version if ("collection", n) in approved
                      else 0 for n in bundle.collections})
        await _insert_samples(session, ctx, approved, samples or {}, actor)
        builder = actor.caller
        if kind == "view":
            view = bundle.views.get(name)
            if view is None:
                raise _refuse("AL-NO-DEFINITION", f"no view {name}", 404)
            return await _view_as(session, ctx, builder, Caller(viewer), view, params,
                                  limit, cursor)
        page = bundle.pages.get(name)
        if page is None:
            raise _refuse("AL-NO-DEFINITION", f"no page {name}", 404)
        blocks = []
        for i, block in enumerate(page.blocks):
            entry: dict = {"index": i, "kind": block.kind}
            if not isinstance(block, TextBlock):
                bound = {}
                for key, value in block.params.items():
                    if isinstance(value, dict):
                        if (params or {}).get(value["page_param"]) is not None:
                            bound[key] = params[value["page_param"]]
                    else:
                        bound[key] = value
                try:
                    entry["result"] = await _view_as(session, ctx, builder, Caller(viewer),
                                                     bundle.views[block.view], bound,
                                                     None, None)
                except RecordError as exc:
                    entry["error"] = {**exc.as_dict(), "status": exc.status}
            blocks.append(entry)
        return {"page": name, "definition": page_for_web(page, bundle), "blocks": blocks}
    finally:
        await session.rollback()


async def _insert_samples(session, ctx: rec.AppContext, approved: dict, samples: Any,
                          actor: Actor) -> None:
    if not isinstance(samples, dict):
        raise _refuse("AL-SAMPLES", "samples map a collection to a list of records", 422)
    for collection, items in samples.items():
        if collection not in ctx.bundle.collections:
            raise _refuse("AL-SAMPLES", f"no collection {collection}", 422)
        if ("collection", collection) in approved:
            raise _refuse("AL-SAMPLES-PUBLISHED", f"{collection} is published; its preview "
                          "reads its real records", 422)
        if not isinstance(items, list) or len(items) > SAMPLES_PER_COLLECTION:
            raise _refuse("AL-SAMPLES", f"at most {SAMPLES_PER_COLLECTION} sample records "
                          "per collection", 422)
        c = ctx.bundle.collections[collection]
        for values in items:
            if not isinstance(values, dict):
                raise RecordError("AD-INVALID-VALUE", "a sample record is an object", 422)
            for f in values:
                if f in SYSTEM_FIELDS:
                    raise RecordError("AD-SYSTEM-FIELD", f"{f} is a system field", 422,
                                      {"field": f})
                if f not in c.fields:
                    raise RecordError("AD-UNKNOWN-FIELD", f"{collection} has no field {f}",
                                      422, {"field": f})
            doc = {f: rec.normalize_value(ctx, c, f, v) for f, v in values.items()}
            doc = {f: v for f, v in doc.items() if v is not None}
            missing = [f for f, spec in c.fields.items() if spec.required and f not in doc]
            if missing:
                raise RecordError("AD-REQUIRED", f"required fields missing: "
                                  f"{', '.join(missing)}", 422, {"fields": missing})
            now = utcnow()
            session.add(AppDataRecord(
                app_id=ctx.app_id, collection=collection, id=f"sample-{uuid.uuid4().hex}",
                current_version=1, created_at=now, updated_at=now, author=actor.principal,
                collection_version=0, doc=doc, **rec.side_columns(c, doc)))
    await session.flush()


async def _view_as(session, ctx: rec.AppContext, builder: Caller, viewer: Caller, view,
                   params, limit, cursor) -> dict:
    """The view as `viewer` sees it, cut to what `builder` may see too."""
    if isinstance(view, ToolViewDef):
        raise RecordError("AD-TOOL-VIEW-NOT-READY", "tool view execution is not ready", 409)
    check_view_access(ctx, builder, view)
    out = await execute_view(session, ctx, viewer, view, params, limit=limit, cursor=cursor)
    if viewer.principal != builder.principal and "rows" in out:
        access = ctx.access(ctx.collection(view.collection), builder)
        for row in out["rows"]:
            for f in row["values"]:
                if not access.can_read(f) and f not in row["restricted"]:
                    row["values"][f] = None
                    row["restricted"].append(f)
    return out


# --- records for the app_data tool ------------------------------------------------------

async def describe_records(session, caller: Caller, app_ref: str) -> dict:
    """The collections, fields, rules and views this caller may use."""
    app = await _app(session, app_ref)
    ctx = await rec.load_app(session, app.id)
    collections, views = [], []
    for name, c in sorted(ctx.bundle.collections.items()):
        access = ctx.access(c, caller)
        if not (access.can_see_rows() or access.can_verb("create")):
            continue
        collections.append({
            "collection": name, "description": c.description, "write_mode": c.write_mode,
            "fields": {f: {"type": spec.type, "required": spec.required,
                           "read": access.can_read(f),
                           "create": access.can_write(f, "create"),
                           "update": c.write_mode == "editable"
                           and access.can_write(f, "update")}
                       for f, spec in c.fields.items()},
            "delete": access.can_verb("delete") and access.tool_allowed("delete"),
            "writers": c.writers.model_dump(exclude_none=True) if c.writers else None,
            "rules": [r.model_dump(by_alias=True) for r in c.rules]})
    for name, v in sorted(ctx.bundle.views.items()):
        if isinstance(v, ToolViewDef):
            if _can_read_view(ctx, caller, v):
                views.append({"view": name, "tool": v.tool, "action": v.action,
                              "sources": v.sources, "count": v.is_count,
                              "params": {p: s.model_dump(exclude_unset=True)
                                         for p, s in v.params.items()}})
            continue
        try:
            check_view_access(ctx, caller, v)
        except RecordError:
            continue
        views.append({"view": name, "collection": v.collection, "count": v.is_count,
                      "params": {p: s.model_dump(exclude_unset=True)
                                 for p, s in v.params.items()}})
    if not collections and not views:
        raise RecordError("AD-FORBIDDEN", f"{caller.principal} has no access to App "
                          f"{app.name}", 403)
    return {"app_id": app.id, "name": app.name, "status": app.status,
            "approved_version": app.approved_version, "collections": collections,
            "views": views}


async def record_create(session, actor: Actor, app_ref: str, *, request_id: str,
                        collection: str, values: dict) -> dict:
    app = await _app(session, app_ref)

    async def work(finish):
        ctx = await rec.load_app(session, app.id)
        c = ctx.collection(collection)
        async with rec.write_lock(session, ctx, [c.collection]):
            record = await rec._insert(session, ctx, actor.caller, c, values)
            await rec.bump_counters(session, ctx.app_id, [c.collection])
            return await finish({"collection": c.collection, "id": record.id,
                                 "version": record.current_version},
                                f"Created {c.collection} {record.id}.")

    return _strip_app(await _build_op(
        session, actor, request_id=request_id, op="record_create", app_id=app.id,
        args={"collection": collection, "values": values}, work=_with_app(app, work)))


async def record_update(session, actor: Actor, app_ref: str, *, request_id: str,
                        collection: str, record_id: str, values: dict,
                        expected_version: int) -> dict:
    app = await _app(session, app_ref)

    async def work(finish):
        ctx = await rec.load_app(session, app.id)
        c = ctx.collection(collection)
        async with rec.write_lock(session, ctx, [c.collection]):
            record = await rec._update(session, ctx, actor.caller, c, record_id, values,
                                       expected_version)
            await rec.bump_counters(session, ctx.app_id, [c.collection])
            return await finish({"collection": c.collection, "id": record.id,
                                 "version": record.current_version},
                                f"Updated {c.collection} {record.id}.")

    return _strip_app(await _build_op(
        session, actor, request_id=request_id, op="record_update", app_id=app.id,
        args={"collection": collection, "id": record_id, "values": values,
              "expected_version": expected_version}, work=_with_app(app, work)))


async def record_delete(session, actor: Actor, app_ref: str, *, request_id: str,
                        collection: str, record_id: str,
                        expected_version: int | None = None,
                        check_plan: Callable[[rec.DeletePlan], None] | None = None) -> dict:
    """Delete one record through its server-computed plan; a `restrict` ref
    refuses it with the plan. `check_plan` may refuse the plan before
    anything is written (a tool call's scope over what it would unlink)."""
    app = await _app(session, app_ref)

    async def work(finish):
        ctx = await rec.load_app(session, app.id)
        expected = {record_id: expected_version} if expected_version is not None else None
        ctx.collection(collection)
        async with rec.delete_lock(session, ctx, collection):
            await rec._authorize_delete(session, ctx, actor.caller, collection, [record_id],
                                        expected)
            plan = await rec.compute_plan(session, ctx, [(collection, record_id)])
            if check_plan is not None:
                check_plan(plan)
            summary = plan.summary(ctx, actor.caller)
            if plan.blocked:
                raise RecordError("AD-REF-RESTRICT", "referenced by records whose ref is "
                                  "on_delete: restrict", 409, summary)
            await rec.execute_plan(session, ctx, actor.caller, plan)
            return await finish({"collection": collection, "id": record_id,
                                 "deleted": True, "plan": summary},
                                f"Deleted {collection} {record_id}.")

    return _strip_app(await _build_op(
        session, actor, request_id=request_id, op="record_delete", app_id=app.id,
        args={"collection": collection, "id": record_id,
              "expected_version": expected_version}, work=_with_app(app, work)))


def _with_app(app: AppDataApp, work):
    """Record receipts carry ids only; the build op still needs the App."""
    async def wrapped(finish):
        async def finish_with_app(result, summary):
            return await finish({**result, "app_id": app.id}, summary)
        return await work(finish_with_app)
    return wrapped


def _strip_app(result: dict) -> dict:
    return {k: v for k, v in result.items() if k != "app_id"}
