"""Proposals (design 39, "The authority model" → "Always proposals",
"Proposals"; revision 3 "Proposals").

What a builder can't self-publish, Kyle approves:

- **Kinds** (D10): a `bundle` of drafts that widens authority or drops stored
  data, a `rollback` to a wider earlier version, or a `transfer` of
  ownership.
- **Freeze.** `propose` stores the change itself, not a pointer to drafts:
  the definition bodies (or the new owner), the approved version and
  authority generation it was computed against, the authority delta in plain
  words and the validation summary, all under one digest. Editing a draft
  afterwards changes nothing Kyle is shown.
- **Approve atomically.** Under the same collection locks a publish takes,
  approval re-checks the frozen change against the App as it is now. If the
  App published, rolled back or changed owner since, if a stored record now
  breaks the change, or if the delta came out different, the proposal goes
  `stale` and nothing publishes. Otherwise it publishes through the lifecycle's
  own publish core: the proposer stays the author, `approved_by` and the
  proposal id are stamped, and the authority generation moves (D11).
- **States.** `open` until approved (`published`), `declined`, `withdrawn` or
  `stale`; every one of those is final.

Every write is a build op with a `request_id`, like the rest of the builder.
Who may call what is checked here too, not only at the routes: approving and
declining are Kyle's alone.
"""
from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import func, select, update

from agentplatform.agentspec import TOOL_APPS
from agentplatform.appdata import authority as A
from agentplatform.appdata import lifecycle as L
from agentplatform.appdata import quotas
from agentplatform.appdata import records as rec
from agentplatform.appdata.lifecycle import Actor, _refuse
from agentplatform.appdata.models import AppDataApp, AppDataProposal
from agentplatform.db import AgentDef, utcnow

# --- reading ----------------------------------------------------------------------------

async def _proposal(session, proposal_id: Any) -> AppDataProposal:
    p = await session.get(AppDataProposal, proposal_id) if isinstance(proposal_id, str) \
        and proposal_id else None
    if p is None:
        raise _refuse("AL-NO-PROPOSAL", f"no proposal {proposal_id}", 404)
    return p


def _require_party(p: AppDataProposal, app: AppDataApp, actor: Actor) -> None:
    """The proposer, the App's owner now, or Kyle. The proposer keeps its
    hold after the App changes hands, so it can still withdraw."""
    if actor.principal not in ("kyle", p.proposer, rec.owner_principal(app)):
        raise _refuse("AL-NOT-OWNER", f"{actor.principal} neither made proposal {p.id} "
                      f"nor owns App {app.name}", 403)


def _require_kyle(actor: Actor) -> None:
    # A call through a tool is never Kyle's own decision.
    if actor.principal != "kyle" or actor.via_tool is not None:
        raise _refuse("AL-NOT-KYLE", "only Kyle approves or declines a proposal, from "
                      "his own session", 403)


def view(p: AppDataProposal) -> dict:
    return {"id": p.id, "app_id": p.app_id, "kind": p.kind, "state": p.state,
            "digest": p.digest, "bundle": p.bundle, "base_version": p.base_version,
            "authority_generation": p.authority_generation, "delta": p.delta,
            "validation": p.validation, "proposer": L.display(p.proposer),
            "run_id": p.run_id, "reason": p.reason,
            "decided_by": L.display(p.decided_by) if p.decided_by else None,
            "decided_at": L._ts(p.decided_at), "outcome": p.outcome,
            "created_at": L._ts(p.created_at)}


async def get(session, actor: Actor, proposal_id: str) -> dict:
    p = await _proposal(session, proposal_id)
    _require_party(p, await session.get(AppDataApp, p.app_id), actor)
    return view(p)


async def list_proposals(session, actor: Actor, app_ref: str, *,
                         state: str | None = None) -> list[dict]:
    """An App's proposals, newest first; the owner's and Kyle's to read."""
    app = await L._app(session, app_ref)
    L._require_reader(app, actor)
    stmt = select(AppDataProposal).where(AppDataProposal.app_id == app.id)
    if state is not None:
        stmt = stmt.where(AppDataProposal.state == state)
    rows = (await session.execute(stmt.order_by(AppDataProposal.created_at.desc(),
                                                AppDataProposal.id))).scalars().all()
    return [view(p) for p in rows]


# --- freezing ---------------------------------------------------------------------------

def _entries(changes: dict) -> list[dict]:
    order = {kind: i for i, kind in enumerate(L.KINDS)}
    return [{"kind": k, "name": n, **({"removed": True} if body is None
                                      else {"definition": body})}
            for (k, n), body in sorted(changes.items(), key=lambda i: (order[i[0][0]],
                                                                       i[0][1]))]


def _changes(bundle: dict) -> dict:
    return {(e["kind"], e["name"]): None if e.get("removed") else e["definition"]
            for e in bundle.get("changes", [])}


def _candidate(current: dict, changes: dict) -> dict:
    out = dict(current)
    for key, body in changes.items():
        if body is None:
            out.pop(key, None)
        else:
            out[key] = body
    return out


def _delta(a: L.Assessment) -> dict:
    return {"added": a.delta["added"], "removed": a.delta["removed"],
            "widening": a.widening}


def _validation(a: L.Assessment) -> dict:
    return {"data_dropping": a.data_dropping, "reindex": a.reindex}


def _dropped(lines: list[str]) -> list[str]:
    """What a data-dropping line drops, without how much: more records holding
    a field being dropped is the change Kyle approved, not a new one."""
    return sorted(line.split(" holds ")[0] for line in lines)


def _digest(app_id: str, kind: str, bundle: dict, base: int | None, generation: int,
            delta: dict) -> str:
    return hashlib.sha256(L._canon({
        "app_id": app_id, "kind": kind, "bundle": bundle, "base_version": base,
        "authority_generation": generation, "delta": delta}).encode()).hexdigest()


def _refuse_frozen(a: L.Assessment, app: AppDataApp, use: str) -> None:
    """What can't be proposed: what doesn't validate or fit the stored records
    (approval would only find it stale), a draft on an old base, and anything
    the builder may publish itself."""
    if a.errors:
        raise _refuse("AL-INVALID", "the App doesn't validate", 422, {"errors": a.errors})
    if a.stale_base:
        raise _refuse("AL-STALE-BASE", "a draft was written against an older version of "
                      "its definition; read the approved one and save the draft again",
                      409, {"stale_base": a.stale_base,
                            "approved_version": app.approved_version})
    if a.record_issues:
        raise _refuse("AL-INCONSISTENT", "records already stored don't fit the new "
                      "definitions", 409, {"record_issues": a.record_issues})
    if not a.needs_proposal:
        raise _refuse("AL-SELF-PUBLISHES", f"this neither widens authority nor drops data, "
                      f"so it needs no approval: use {use}", 409, {"suggest": use})


async def _transfer_refusal(session, app: AppDataApp, target: Any) -> str | None:
    """Why `target` can't own the App, or None: the new owner is Kyle or an
    agent holding `apps`, and not the owner it already has."""
    if not isinstance(target, str) or not L.PRINCIPAL_RE.fullmatch(target):
        return f"{target!r} isn't an owner: name kyle or agent:<name>"
    if target == rec.owner_principal(app):
        return f"{target} already owns App {app.name}"
    if target == "kyle":
        return None
    row = await session.get(AgentDef, target.removeprefix("agent:"))
    if row is None or row.enabled is False:
        return f"no enabled agent {target}"
    if TOOL_APPS not in (row.platform_tools or []):
        return f"{target} doesn't hold the apps tool, so it couldn't maintain the App"
    return None


async def _open_counts(session, app: AppDataApp) -> tuple[int, int]:
    open_ = AppDataProposal.state == "open"
    for_app = (await session.execute(select(func.count()).select_from(AppDataProposal)
                                     .where(AppDataProposal.app_id == app.id, open_))
               ).scalar_one()
    for_owner = (await session.execute(
        select(func.count()).select_from(AppDataProposal)
        .join(AppDataApp, AppDataApp.id == AppDataProposal.app_id)
        .where(open_, AppDataApp.owner_kind == app.owner_kind,
               AppDataApp.owner_id == app.owner_id))).scalar_one()
    return for_app, for_owner


async def propose(session, actor: Actor, app_ref: str, *, request_id: str, only=None,
                  rollback_to: Any = None, transfer_to: Any = None,
                  reason: str = "") -> dict:
    """Freeze the drafts (or `only` some), a rollback to `rollback_to`, or a
    transfer to `transfer_to` as a proposal for Kyle. Returns it, digest
    included."""
    app = await L._app(session, app_ref)
    L._require_owner(app, actor)
    args = {"only": only, "rollback_to": rollback_to, "transfer_to": transfer_to,
            "reason": reason}

    async def work(finish):
        L._require_active(app)
        if not isinstance(reason, str) or len(reason) > L.REASON_MAX:
            raise _refuse("AL-ARGS", f"a reason is at most {L.REASON_MAX} characters", 422)
        if sum(x is not None for x in (only, rollback_to, transfer_to)) > 1:
            raise _refuse("AL-ARGS", "propose drafts (`only`), a rollback or a transfer, "
                          "one at a time", 422)
        rows = await L._rows(session, app.id)
        current = L._bodies(L._approved(rows, app))
        if transfer_to is not None:
            why = await _transfer_refusal(session, app, transfer_to)
            if why:
                raise _refuse("AL-TRANSFER-TARGET", why, 422, {"transfer_to": transfer_to})
            kind, bundle = "transfer", {"transfer_to": transfer_to}
            owner = rec.owner_principal(app)
            delta = {"added": [f"{transfer_to} owns the App"],
                     "removed": [f"{owner} owns the App"],
                     "widening": [f"ownership transfer: {owner} to {transfer_to}"]}
            validation = {"data_dropping": [], "reindex": []}
        else:
            if rollback_to is not None:
                if app.approved_version is None or isinstance(rollback_to, bool) \
                        or not isinstance(rollback_to, int) \
                        or not 1 <= rollback_to < app.approved_version:
                    raise _refuse("AL-ROLLBACK-TARGET", "roll back to an earlier approved "
                                  f"version, 1 to {(app.approved_version or 1) - 1}", 422)
                target = L._bodies(rec.state_at(rows, rollback_to))
                a = await L._assess(session, app, current, target)
                _refuse_frozen(a, app, "rollback")
                kind = "rollback"
                bundle = {"rollback_to": rollback_to,
                          "changes": _entries(L.rollback_changes(a, current, target))}
            else:
                drafts = L._selected(L._drafts(rows), only)
                changes = {k: r for k, r in drafts.items() if L._changes(r, current)}
                if not changes:
                    raise _refuse("AL-NOTHING-TO-PROPOSE", "no draft changes the approved "
                                  "state", 409)
                a = await L._assess(session, app, current, L._overlay(current, changes),
                                    drafts=changes, rows=rows)
                _refuse_frozen(a, app, "publish")
                kind = "bundle"
                bundle = {"changes": _entries({k: None if r.removed else r.body
                                               for k, r in changes.items()})}
            delta, validation = _delta(a), _validation(a)
        for_app, for_owner = await _open_counts(session, app)
        await quotas.check_new_proposal(session, app.id, rec.owner_principal(app),
                                        open_for_app=for_app, open_for_owner=for_owner)
        p = AppDataProposal(
            app_id=app.id, kind=kind, bundle=bundle, base_version=app.approved_version,
            authority_generation=app.authority_generation, delta=delta,
            validation=validation, state="open", proposer=actor.principal,
            run_id=actor.run_id, reason=reason,
            digest=_digest(app.id, kind, bundle, app.approved_version,
                           app.authority_generation, delta))
        session.add(p)
        await session.flush()
        return await finish(view(p), f"Proposed a {kind} for App {app.name}.")

    return await L._build_op(session, actor, request_id=request_id, op="propose",
                             app_id=app.id, args=args, work=work)


# --- deciding ---------------------------------------------------------------------------

def _require_open(p: AppDataProposal) -> None:
    if p.state != "open":
        raise _refuse("AL-PROPOSAL-CLOSED", f"proposal {p.id} is {p.state}; a decided "
                      "proposal stays decided, so propose again", 409, {"state": p.state})


def _close(p: AppDataProposal, state: str, actor: Actor, outcome: dict | None) -> None:
    now = utcnow()
    p.state, p.decided_by, p.decided_at, p.outcome, p.updated_at = (
        state, actor.principal, now, outcome, now)


def _moved(app: AppDataApp, p: AppDataProposal) -> list[str]:
    why = []
    if app.approved_version != p.base_version:
        why.append(f"the App is at approved version {app.approved_version}, not "
                   f"{p.base_version} as proposed")
    if app.authority_generation != p.authority_generation:
        why.append("the App's authority or owner changed since it was proposed")
    return why


def _drift(a: L.Assessment, p: AppDataProposal) -> list[str]:
    """What a re-check found that wasn't in the proposal Kyle reviewed."""
    why = [f"{e.get('code')}: {e.get('message')}" for e in a.errors]
    why += [i["message"] for i in a.record_issues]
    if not a.errors and (_delta(a) != p.delta or _dropped(a.data_dropping)
                         != _dropped(p.validation.get("data_dropping", []))):
        why.append("the authority delta or the data it drops is no longer what was "
                   "proposed")
    return why


async def _clear_drafts(session, app_id: str, changes: dict) -> None:
    """Drop the drafts the approval published. One edited since proposing
    wasn't published, so it stays for the builder (on a now-stale base)."""
    for draft in L._drafts(await L._rows(session, app_id)).values():
        key = (draft.kind, draft.name)
        if key not in changes:
            continue
        body = changes[key]
        if (body is None and draft.removed) or (body is not None and not draft.removed
                                                and draft.body == body):
            await session.delete(draft)


async def approve(session, actor: Actor, proposal_id: str, *, request_id: str,
                  digest: Any) -> dict:
    """Kyle approves the proposal he was shown, named by its `digest`. Either
    it publishes in one transaction, or it's marked `stale` and nothing does;
    the result's `state` says which."""
    _require_kyle(actor)
    p = await _proposal(session, proposal_id)
    app = await session.get(AppDataApp, p.app_id)

    async def work(finish):
        _require_open(p)
        if digest != p.digest:
            raise _refuse("AL-PROPOSAL-DIGEST", "that isn't the digest of this proposal; "
                          "review it again", 409)
        L._require_active(app)

        async def stale(why: list[str]) -> dict:
            _close(p, "stale", actor, {"why": why})
            await session.flush()
            return await finish({**view(p), "published": False, "why": why},
                                f"Proposal {p.id} for App {app.name} went stale.")

        why = _moved(app, p)
        if p.kind == "transfer":
            target = p.bundle["transfer_to"]
            refusal = None if why else await _transfer_refusal(session, app, target)
            if why or refusal:
                return await stale(why or [refusal])
            old = rec.owner_principal(app)
            kind, owner_id = ("kyle", "kyle") if target == "kyle" \
                else ("agent", target.removeprefix("agent:"))
            moved = await session.execute(
                update(AppDataApp).where(
                    AppDataApp.id == app.id,
                    AppDataApp.authority_generation == p.authority_generation,
                    (AppDataApp.approved_version.is_(None) if p.base_version is None
                     else AppDataApp.approved_version == p.base_version))
                .values(owner_kind=kind, owner_id=owner_id, updated_at=utcnow(),
                        authority_generation=AppDataApp.authority_generation + 1)
                .execution_options(synchronize_session=False))
            if moved.rowcount != 1:
                raise _refuse("AL-CONFLICT", "the App changed while approving; read the "
                              "proposal again", 409)
            await quotas.transfer_owner(session, app.id, old, target)
            await session.refresh(app)
            _close(p, "published", actor, None)
            await session.flush()
            return await finish({**view(p), "published": True, "owner": target,
                                 "authority_generation": app.authority_generation},
                                f"Transferred App {app.name} from {old} to {target}.")

        if why:
            return await stale(why)
        rows = await L._rows(session, app.id)
        current = L._bodies(L._approved(rows, app))
        changes = _changes(p.bundle)
        candidate = _candidate(current, changes)
        async with L._consistency_lock(session, app.id, current, candidate):
            a = await L._assess(session, app, current, candidate)
            why = _drift(a, p)
            if why:
                return await stale(why)
            version, entries = await L._publish_core(
                session, app, a, expected=p.base_version, changes=changes,
                author=p.proposer, run_id=p.run_id,
                reason=p.reason or f"proposal {p.id}", approved_by=actor.principal,
                proposal_id=p.id)
            await _clear_drafts(session, app.id, changes)
            _close(p, "published", actor, None)
            await session.flush()
            return await finish({**view(p), "published": True, "approved_version": version,
                                 "authority_generation": app.authority_generation,
                                 "entries": entries, "facts_digest": A.digest(a.facts)},
                                L._summary_line(f"Approved proposal {p.id}: published",
                                                version, entries))

    return await L._build_op(session, actor, request_id=request_id, op="proposal_approve",
                             app_id=p.app_id, args={"proposal": p.id, "digest": digest},
                             work=work)


async def decline(session, actor: Actor, proposal_id: str, *, request_id: str,
                  reason: str = "") -> dict:
    _require_kyle(actor)
    p = await _proposal(session, proposal_id)

    async def work(finish):
        _require_open(p)
        if not isinstance(reason, str) or len(reason) > L.REASON_MAX:
            raise _refuse("AL-ARGS", f"a reason is at most {L.REASON_MAX} characters", 422)
        _close(p, "declined", actor, {"reason": reason})
        await session.flush()
        return await finish(view(p), f"Declined proposal {p.id}"
                            + (f": {reason}" if reason else "."))

    return await L._build_op(session, actor, request_id=request_id, op="proposal_decline",
                             app_id=p.app_id, args={"proposal": p.id, "reason": reason},
                             work=work)


async def withdraw(session, actor: Actor, proposal_id: str, *, request_id: str) -> dict:
    """The proposer or the App's owner takes an open proposal back."""
    p = await _proposal(session, proposal_id)
    app = await session.get(AppDataApp, p.app_id)
    if actor.principal not in (p.proposer, rec.owner_principal(app)):
        raise _refuse("AL-NOT-OWNER", f"only the proposer or the App's owner withdraws "
                      f"proposal {p.id}", 403)

    async def work(finish):
        _require_open(p)
        _close(p, "withdrawn", actor, None)
        await session.flush()
        return await finish(view(p), f"Withdrew proposal {p.id}.")

    return await L._build_op(session, actor, request_id=request_id, op="proposal_withdraw",
                             app_id=p.app_id, args={"proposal": p.id}, work=work)
