"""Artifacts in records (design 39, "Collections" → "Artifact fields", and
"Trust boundaries": App-owned artifacts).

An artifact id written into an `artifact` field makes the artifact
App-owned. What is held down here:

- ownership: the first referencing field owns it; a second field may refer to
  it only when its readers are a subset of the owning field's;
- claiming: an agent claims only its own ordinary artifacts, never another
  App's, never one an agent wears as its face;
- every artifact route (metadata, content, thumb, resource), the list, the
  stats, the writes and the event feed authorize it through the owning field,
  so the `artifacts` grant alone doesn't reach it;
- the 64 MiB cap, counted against the App and not the platform total;
- deletion when the last referencing record goes: delete plans, retention,
  and an update that clears or replaces the value;
- ordinary artifacts behave exactly as before.
"""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from agentplatform import artifact_store as store
from agentplatform.api.artifacts_feed import STREAM
from agentplatform.appdata import artifacts as app_artifacts
from agentplatform.appdata import batch as batch_mod
from agentplatform.appdata import quotas
from agentplatform.appdata.access import Caller, RecordError
from agentplatform.appdata.models import (AppDataApp, AppDataArtifact, AppDataArtifactRef,
                                          AppDataDefinition)
from agentplatform.appdata.records import (create_record, delete_record, load_app,
                                           update_record)
from agentplatform.appdata.retention import prune_app
from agentplatform.config import Settings
from agentplatform.db import AgentDef, Artifact, ArtifactBlob

from .test_artifact_store import png_bytes
from .test_artifacts_api import _agent_headers, upload
from .test_relay_api import _human_token
from .test_relay_sse import sse  # noqa: F401

PAI = Caller("agent:pai")
BOB = Caller("agent:bob")
KYLE = Caller("kyle")
ARTIFACTS_GRANT = "mcp__platform__artifacts"
RELAY_GRANT = "mcp__platform__relay"

WRITERS = ["owner", "kyle"]


def docs(**extra):
    """`file` keeps the default readers (owner, kyle); `secret` is narrower
    (owner only); `shared` is wider (bob, carol and the QA login too)."""
    body = {"collection": "docs",
            "access": {"read": ["owner", "kyle"], "create": WRITERS, "update": WRITERS,
                       "delete": WRITERS},
            "fields": {"title": {"type": "string", "max": 40},
                       "file": {"type": "artifact"},
                       "secret": {"type": "artifact", "access": {"read": ["owner"]}},
                       "shared": {"type": "artifact", "access": {
                           "read": ["owner", "kyle", "agent:bob", "agent:carol",
                                    "login:qa"]}}}}
    body.update(extra)
    return body


async def make_app(sf, collections, *, owner="agent:pai", status="active"):
    kind, owner_id = ("kyle", "kyle") if owner == "kyle" else ("agent", owner[6:])
    app_id = uuid.uuid4().hex
    async with sf() as s:
        s.add(AppDataApp(id=app_id, name=f"app_{app_id[:12]}", owner_kind=kind,
                         owner_id=owner_id, status=status))
        for body in collections:
            s.add(AppDataDefinition(app_id=app_id, kind="collection",
                                    name=body["collection"], version=1, body=body,
                                    state="published", author=owner))
        await s.commit()
        return await load_app(s, app_id)


async def plain(sf, owner="agent:pai", data=None) -> str:
    """An ordinary artifact, as the REST upload would store it."""
    async with sf() as s:
        row = await store.create(s, data=data or png_bytes(), owner=owner, name="pic.png")
        return row.id


async def refused(code, awaitable):
    with pytest.raises(RecordError) as caught:
        await awaitable
    assert caught.value.code == code, caught.value
    return caught.value


async def ownership(sf, artifact_id):
    async with sf() as s:
        return await s.get(AppDataArtifact, artifact_id)


async def refs(sf, artifact_id):
    async with sf() as s:
        return sorted((r.collection, r.record_id, r.field) for r in (await s.execute(
            select(AppDataArtifactRef).where(AppDataArtifactRef.artifact_id == artifact_id)
        )).scalars())


async def gone(sf, artifact_id) -> bool:
    async with sf() as s:
        return (await s.get(Artifact, artifact_id) is None
                and await s.get(ArtifactBlob, artifact_id) is None
                and await s.get(AppDataArtifact, artifact_id) is None)


# --- ownership ---------------------------------------------------------------------------

async def test_writing_an_artifact_id_makes_it_app_owned(sf):
    ctx = await make_app(sf, [docs()])
    aid = await plain(sf)
    async with sf() as s:
        row = await create_record(s, ctx, PAI, "docs", {"title": "a", "file": aid})
    own = await ownership(sf, aid)
    assert (own.app_id, own.collection, own.field) == (ctx.app_id, "docs", "file")
    assert own.size == len(png_bytes())
    assert await refs(sf, aid) == [("docs", row["id"], "file")]
    assert row["values"]["file"] == aid


async def test_the_first_reference_owns_it_and_a_narrower_field_may_refer(sf):
    ctx = await make_app(sf, [docs()])
    aid = await plain(sf)
    async with sf() as s:
        first = await create_record(s, ctx, PAI, "docs", {"file": aid})
    async with sf() as s:
        second = await create_record(s, ctx, PAI, "docs", {"secret": aid})
    own = await ownership(sf, aid)
    assert (own.collection, own.field) == ("docs", "file")
    assert await refs(sf, aid) == sorted([("docs", first["id"], "file"),
                                          ("docs", second["id"], "secret")])


async def test_a_wider_field_may_not_refer_to_it(sf):
    """A second reference never widens access: `shared` adds bob and the QA
    login to `file`'s readers, so it's refused and nothing is written."""
    ctx = await make_app(sf, [docs()])
    aid = await plain(sf)
    async with sf() as s:
        await create_record(s, ctx, PAI, "docs", {"file": aid})
    async with sf() as s:
        err = await refused("AD-ARTIFACT-WIDENS", create_record(s, ctx, PAI, "docs",
                                                                {"shared": aid}))
    assert err.status == 403 and err.detail["field"] == "shared"
    assert len(await refs(sf, aid)) == 1
    # Nor through an update.
    async with sf() as s:
        row = await create_record(s, ctx, PAI, "docs", {"title": "x"})
    async with sf() as s:
        await refused("AD-ARTIFACT-WIDENS", update_record(
            s, ctx, PAI, "docs", row["id"], {"shared": aid}, expected_version=1))


async def test_the_subset_is_of_the_principals_as_written(sf):
    """`owner` and the owner's own name are different readers: an ownership
    transfer moves one and not the other, so neither stands in for the other."""
    ctx = await make_app(sf, [docs(fields={
        "a": {"type": "artifact", "access": {"read": ["owner"]}},
        "b": {"type": "artifact", "access": {"read": ["agent:pai"]}}})])
    aid = await plain(sf)
    async with sf() as s:
        await create_record(s, ctx, PAI, "docs", {"a": aid})
    async with sf() as s:
        await refused("AD-ARTIFACT-WIDENS", create_record(s, ctx, PAI, "docs", {"b": aid}))


async def test_the_owning_field_refers_again_freely(sf):
    ctx = await make_app(sf, [docs()])
    aid = await plain(sf)
    async with sf() as s:
        await create_record(s, ctx, PAI, "docs", {"shared": aid})
    async with sf() as s:
        await create_record(s, ctx, PAI, "docs", {"shared": aid})
    assert len(await refs(sf, aid)) == 2


async def test_another_field_must_be_able_to_read_the_owning_one(sf):
    """Bob writes `secret`-shaped fields but can't read `file`: referring to
    an artifact it can't read is refused like an unknown id."""
    ctx = await make_app(sf, [docs(
        access={"read": ["owner"], "create": ["owner", "agent:bob"],
                "update": ["owner"], "delete": ["owner"]},
        fields={"file": {"type": "artifact"},
                "mine": {"type": "artifact", "access": {"read": ["owner"]}}})])
    aid = await plain(sf)
    async with sf() as s:
        await create_record(s, ctx, PAI, "docs", {"file": aid})
    async with sf() as s:
        await refused("AD-ARTIFACT-UNAVAILABLE", create_record(s, ctx, BOB, "docs",
                                                               {"mine": aid}))


async def test_an_agent_claims_only_its_own_ordinary_artifacts(sf):
    ctx = await make_app(sf, [docs(access={"read": ["owner", "kyle"],
                                           "create": ["owner", "kyle", "agent:bob"],
                                           "update": WRITERS, "delete": WRITERS})])
    pais = await plain(sf, owner="agent:pai")
    async with sf() as s:
        await refused("AD-ARTIFACT-UNAVAILABLE", create_record(s, ctx, BOB, "docs",
                                                               {"file": pais}))
    assert await ownership(sf, pais) is None
    # Kyle may claim anyone's, as he may already rename or delete anyone's.
    async with sf() as s:
        await create_record(s, ctx, KYLE, "docs", {"file": pais})
    assert (await ownership(sf, pais)).field == "file"


@pytest.mark.parametrize("how", ["unknown", "deleted"])
async def test_an_unknown_or_deleted_artifact_is_refused(sf, how):
    ctx = await make_app(sf, [docs()])
    aid = await plain(sf)
    if how == "deleted":
        async with sf() as s:
            await store.soft_delete(s, aid)
    else:
        aid = uuid.uuid4().hex
    async with sf() as s:
        await refused("AD-ARTIFACT-UNAVAILABLE", create_record(s, ctx, PAI, "docs",
                                                               {"file": aid}))


async def test_another_apps_artifact_is_refused(sf):
    one = await make_app(sf, [docs()])
    two = await make_app(sf, [docs()])
    aid = await plain(sf)
    async with sf() as s:
        await create_record(s, one, PAI, "docs", {"file": aid})
    async with sf() as s:
        await refused("AD-ARTIFACT-UNAVAILABLE", create_record(s, two, PAI, "docs",
                                                               {"file": aid}))


async def test_an_agents_face_is_not_claimed(sf, seed_agent):
    """An agent's picture is shown to everyone; an App field can't take it
    private (and later delete it out from under the face)."""
    ctx = await make_app(sf, [docs()])
    aid = await plain(sf)
    await seed_agent("pai", image_artifact_id=aid)
    async with sf() as s:
        await refused("AD-ARTIFACT-IN-USE", create_record(s, ctx, PAI, "docs",
                                                          {"file": aid}))


async def test_a_refused_write_claims_nothing(sf):
    ctx = await make_app(sf, [docs(fields={"title": {"type": "string", "required": True},
                                           "file": {"type": "artifact"}})])
    aid = await plain(sf)
    async with sf() as s:
        await refused("AD-REQUIRED", create_record(s, ctx, PAI, "docs", {"file": aid}))
    assert await ownership(sf, aid) is None and await refs(sf, aid) == []


# --- deletion on the last reference ------------------------------------------------------

async def test_the_artifact_goes_with_its_last_referencing_record(sf):
    ctx = await make_app(sf, [docs()])
    aid = await plain(sf)
    async with sf() as s:
        a = await create_record(s, ctx, PAI, "docs", {"file": aid})
    async with sf() as s:
        b = await create_record(s, ctx, PAI, "docs", {"secret": aid})
    async with sf() as s:
        await delete_record(s, ctx, PAI, "docs", a["id"])
    assert not await gone(sf, aid)
    assert await refs(sf, aid) == [("docs", b["id"], "secret")]
    async with sf() as s:
        await delete_record(s, ctx, PAI, "docs", b["id"])
    assert await gone(sf, aid)


async def test_a_blocked_delete_keeps_the_artifact(sf):
    ctx = await make_app(sf, [docs(), {"collection": "notes", "fields": {
        "doc": {"type": "ref", "collection": "docs"}}}])
    aid = await plain(sf)
    async with sf() as s:
        a = await create_record(s, ctx, PAI, "docs", {"file": aid})
    async with sf() as s:
        await create_record(s, ctx, PAI, "notes", {"doc": a["id"]})
    async with sf() as s:
        await refused("AD-REF-RESTRICT", delete_record(s, ctx, PAI, "docs", a["id"]))
    assert not await gone(sf, aid)


async def test_clearing_or_replacing_the_value_deletes_the_old_artifact(sf):
    ctx = await make_app(sf, [docs()])
    old, new = await plain(sf), await plain(sf)
    async with sf() as s:
        row = await create_record(s, ctx, PAI, "docs", {"file": old})
    async with sf() as s:
        await update_record(s, ctx, PAI, "docs", row["id"], {"file": new},
                            expected_version=1)
    assert await gone(sf, old)
    assert (await ownership(sf, new)).field == "file"
    async with sf() as s:
        await update_record(s, ctx, PAI, "docs", row["id"], {"file": None},
                            expected_version=2)
    assert await gone(sf, new)


async def test_an_immutable_upsert_replacing_the_value_moves_ownership(sf):
    # A batch upsert into an immutable collection replaces the record whole;
    # it must claim the new artifact and release the old one as an update does.
    ctx = await make_app(sf, [docs(write_mode="immutable", rules=[
        {"kind": "unique", "fields": ["title"]}])])
    old, new = await plain(sf), await plain(sf)
    async with sf() as s:
        await batch_mod.batch(s, ctx, PAI, "docs", [{"title": "a", "file": old}],
                              mode="upsert", key=["title"])
    async with sf() as s:
        await batch_mod.batch(s, ctx, PAI, "docs", [{"title": "a", "file": new}],
                              mode="upsert", key=["title"])
    assert await gone(sf, old)
    assert (await ownership(sf, new)).field == "file"
    assert len(await refs(sf, new)) == 1


async def test_moving_it_between_fields_of_one_record_keeps_it(sf):
    ctx = await make_app(sf, [docs()])
    aid = await plain(sf)
    async with sf() as s:
        row = await create_record(s, ctx, PAI, "docs", {"file": aid})
    async with sf() as s:
        await update_record(s, ctx, PAI, "docs", row["id"], {"file": None, "secret": aid},
                            expected_version=1)
    assert not await gone(sf, aid)
    assert await refs(sf, aid) == [("docs", row["id"], "secret")]


async def test_retention_deletes_the_artifacts_it_prunes(sf):
    ctx = await make_app(sf, [docs(retention={"max_records": 1})])
    first, second = await plain(sf), await plain(sf)
    async with sf() as s:
        await create_record(s, ctx, PAI, "docs", {"file": first})
    async with sf() as s:
        await create_record(s, ctx, PAI, "docs", {"file": second})
    async with sf() as s:
        await prune_app(s, ctx)
    assert await gone(sf, first) and not await gone(sf, second)


async def test_an_upload_never_referenced_is_swept_after_a_day(sf):
    ctx = await make_app(sf, [docs()])
    async with sf() as s:
        orphan = await app_artifacts.upload(s, ctx, PAI, "docs", "file", b"x" * 10,
                                            name="o.bin")
    async with sf() as s:
        used = await app_artifacts.upload(s, ctx, PAI, "docs", "file", b"y" * 10,
                                          name="u.bin")
        await create_record(s, ctx, PAI, "docs", {"file": used.id})
    async with sf() as s:
        await prune_app(s, ctx)
    assert not await gone(sf, orphan.id)
    async with sf() as s:
        await prune_app(s, ctx, now=app_artifacts.utcnow() + timedelta(hours=25))
    assert await gone(sf, orphan.id) and not await gone(sf, used.id)


async def _used(sf, ctx) -> dict:
    """(records, bytes) charged to the App and to its owner."""
    out = {}
    async with sf() as s:
        for scope_kind, scope_id in (("app", ctx.app_id), ("owner", ctx.owner)):
            used = (await quotas.describe(s, scope_kind, scope_id))["used"]
            out[scope_kind] = (used["records"], used["bytes"])
    return out


async def test_deleting_the_record_releases_its_artifacts_bytes(sf):
    ctx = await make_app(sf, [docs()])
    aid = await plain(sf, data=b"p" * 500)
    async with sf() as s:
        row = await create_record(s, ctx, PAI, "docs", {"title": "a", "file": aid})
    doc = quotas.doc_bytes({"title": "a", "file": aid})
    assert await _used(sf, ctx) == {"app": (1, 500 + doc), "owner": (1, 500 + doc)}
    async with sf() as s:
        await delete_record(s, ctx, PAI, "docs", row["id"])
    assert await gone(sf, aid)
    assert await _used(sf, ctx) == {"app": (0, 0), "owner": (0, 0)}


async def test_the_orphan_sweep_releases_an_uploads_bytes(sf):
    ctx = await make_app(sf, [docs()])
    async with sf() as s:
        orphan = await app_artifacts.upload(s, ctx, PAI, "docs", "file", b"x" * 300,
                                            name="o.bin")
    assert await _used(sf, ctx) == {"app": (0, 300), "owner": (0, 300)}
    async with sf() as s:
        await prune_app(s, ctx, now=app_artifacts.utcnow() + timedelta(hours=25))
    assert await gone(sf, orphan.id)
    assert await _used(sf, ctx) == {"app": (0, 0), "owner": (0, 0)}


# --- the upload helper, the cap and the App's bytes ----------------------------------------

async def test_upload_makes_an_app_owned_artifact_in_one_step(sf, producer):
    ctx = await make_app(sf, [docs()])
    async with sf() as s:
        row = await app_artifacts.upload(s, ctx, Caller("agent:pai", via_tool="tool:backtest"),
                                         "docs", "file", b"a,b\n1,2\n", name="d.csv",
                                         claimed_mime="text/csv", run_id=None,
                                         producer=producer)
    assert (row.owner, row.mime, row.source) == ("agent:pai", "text/csv", "tool")
    own = await ownership(sf, row.id)
    assert (own.app_id, own.collection, own.field, own.size) == (ctx.app_id, "docs", "file", 8)
    # The event goes out after the ownership commits, and says whose it is,
    # so the feed can filter it even after the artifact is gone.
    event = [e for e in producer.envelopes if e["type"] == "artifacts.event"][-1]["data"]
    assert event["event"] == "created" and event["artifact"]["id"] == row.id
    assert event["app"] == {"app_id": ctx.app_id, "collection": "docs", "field": "file"}


@pytest.mark.parametrize("collection, field, caller, code", [
    ("docs", "title", PAI, "AD-NOT-ARTIFACT-FIELD"),
    ("docs", "nope", PAI, "AD-NOT-ARTIFACT-FIELD"),
    ("nope", "file", PAI, "AD-NO-COLLECTION"),
    ("docs", "file", BOB, "AD-FIELD-FORBIDDEN"),
])
async def test_upload_refusals(sf, collection, field, caller, code):
    ctx = await make_app(sf, [docs()])
    async with sf() as s:
        await refused(code, app_artifacts.upload(s, ctx, caller, collection, field, b"x"))
        assert (await s.execute(select(func.count(Artifact.id)))).scalar() == 0


async def test_upload_honours_tool_only_writers(sf):
    ctx = await make_app(sf, [docs(writers={"create": ["tool:backtest"],
                                            "update": ["tool:backtest"]})])
    async with sf() as s:
        await refused("AD-TOOL-ONLY", app_artifacts.upload(s, ctx, PAI, "docs", "file", b"x"))
    async with sf() as s:
        await app_artifacts.upload(s, ctx, Caller("agent:pai", via_tool="tool:backtest"),
                                   "docs", "file", b"x")


async def test_upload_refuses_a_retired_app(sf):
    ctx = await make_app(sf, [docs()], status="retired")
    async with sf() as s:
        await refused("AD-APP-RETIRED", app_artifacts.upload(s, ctx, PAI, "docs", "file",
                                                             b"x"))


async def test_app_owned_artifacts_take_64_mib_not_8(sf):
    settings = Settings()
    assert settings.app_artifacts_max_bytes == 64 * 1024 * 1024
    ctx = await make_app(sf, [docs()])
    big = b"x" * (settings.artifacts_max_bytes + 1)
    async with sf() as s:
        row = await app_artifacts.upload(s, ctx, PAI, "docs", "file", big, settings=settings)
    assert row.size == len(big)
    small = Settings(app_artifacts_max_bytes=1024)
    async with sf() as s:
        err = await refused("AD-ARTIFACT-TOO-LARGE", app_artifacts.upload(
            s, ctx, PAI, "docs", "file", b"x" * 1025, settings=small))
    assert err.status == 413


async def test_app_bytes_count_against_the_app_not_the_platform_total(sf):
    ctx = await make_app(sf, [docs()])
    await plain(sf, data=b"p" * 100)
    async with sf() as s:
        await app_artifacts.upload(s, ctx, PAI, "docs", "file", b"x" * 300)
    claimed = await plain(sf, data=b"c" * 50)
    async with sf() as s:
        await create_record(s, ctx, PAI, "docs", {"file": claimed})
    async with sf() as s:
        assert await store.usage(s) == (1, 100)
        assert await app_artifacts.app_bytes(s, ctx.app_id) == 350
    # A platform store at its cap doesn't stop an App upload: the App's quota
    # is what bounds it (A11).
    full = Settings(artifacts_total_max_bytes=100)
    async with sf() as s:
        await app_artifacts.upload(s, ctx, PAI, "docs", "file", b"x" * 10, settings=full)


async def test_the_quota_hook_sees_every_byte_and_can_refuse(sf, monkeypatch):
    ctx = await make_app(sf, [docs()])
    seen = []

    async def hook(session, ctx_, adding):
        seen.append((ctx_.app_id, adding))
        if adding > 100:
            raise RecordError("AD-QUOTA", "over", 507)
    monkeypatch.setattr(app_artifacts, "check_app_bytes", hook)
    async with sf() as s:
        await app_artifacts.upload(s, ctx, PAI, "docs", "file", b"x" * 10)
    aid = await plain(sf, data=b"y" * 200)
    async with sf() as s:
        await refused("AD-QUOTA", create_record(s, ctx, PAI, "docs", {"file": aid}))
    async with sf() as s:
        await refused("AD-QUOTA", app_artifacts.upload(s, ctx, PAI, "docs", "file",
                                                       b"z" * 101))
    assert seen == [(ctx.app_id, 10), (ctx.app_id, 200), (ctx.app_id, 101)]
    assert await ownership(sf, aid) is None


# --- the routes --------------------------------------------------------------------------

def _routes(aid):
    return {"metadata": f"/api/artifacts/{aid}", "content": f"/api/artifacts/{aid}/content",
            "thumb": f"/api/artifacts/{aid}/thumb",
            "resource": f"/api/artifacts/{aid}/resource"}


async def _owned(sf, ctx, field, owner="agent:pai"):
    """An image App-owned through `field`, uploaded by its App's owner."""
    async with sf() as s:
        row = await app_artifacts.upload(s, ctx, Caller(owner), "docs", field, png_bytes(),
                                         name="p.png")
    return row.id


@pytest.mark.parametrize("route", ["metadata", "content", "thumb", "resource"])
async def test_every_route_authorizes_through_the_owning_field(
        sf, seed_agent, agent_store, token_client, admin_client, route):
    ctx = await make_app(sf, [docs()])
    file_ = await _owned(sf, ctx, "file")
    secret = await _owned(sf, ctx, "secret")
    shared = await _owned(sf, ctx, "shared")
    pai = await _agent_headers(sf, seed_agent, agent_store, "pai")
    bob = await _agent_headers(sf, seed_agent, agent_store, "bob")
    # Carol holds no artifacts grant at all: her App fact is what reads it.
    carol = await _agent_headers(sf, seed_agent, agent_store, "carol",
                                 grants=(RELAY_GRANT,))
    reader = await _human_token(sf, "someone", "reader")
    admin_key = await _human_token(sf, "ops", "admin")

    async def status(path, headers=None):
        client = admin_client if headers is None else token_client
        return (await client.get(path, headers=headers or {})).status_code

    # Kyle's session reads `file` and `shared`, not `secret` (owner only).
    assert await status(_routes(file_)[route]) == 200
    assert await status(_routes(shared)[route]) == 200
    assert await status(_routes(secret)[route]) == 404
    # The App's owner reads all three, through `owner`.
    for aid in (file_, secret, shared):
        assert await status(_routes(aid)[route], pai) == 200
    # Bob, holding the artifacts grant, reads only `shared`.
    assert await status(_routes(file_)[route], bob) == 404
    assert await status(_routes(shared)[route], bob) == 200
    # Without the grant, the field is still the authority.
    assert await status(_routes(shared)[route], carol) == 200
    assert await status(_routes(file_)[route], carol) == 403
    # A human API key, even an admin one, is not Kyle and names no App principal.
    assert await status(_routes(file_)[route], reader) == 404
    assert await status(_routes(file_)[route], admin_key) == 404


async def test_the_qa_login_reads_what_it_is_named_on(sf, client, admin_client):
    ctx = await make_app(sf, [docs()])
    shared, file_ = await _owned(sf, ctx, "shared"), await _owned(sf, ctx, "file")
    from agentplatform.api.auth import ph
    from agentplatform.db import Principal
    async with sf() as s:
        s.add(Principal(name="qa", role="reader", password_hash=ph.hash("qa-pw-12345")))
        await s.commit()
    r = await client.post("/api/login", json={"principal": "qa", "password": "qa-pw-12345"})
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/artifacts/{shared}/content")).status_code == 200
    assert (await client.get(f"/api/artifacts/{file_}/content")).status_code == 404


async def test_the_owning_field_gone_from_the_definition_fails_closed(sf, admin_client):
    ctx = await make_app(sf, [docs()])
    aid = await _owned(sf, ctx, "file")
    async with sf() as s:
        s.add(AppDataDefinition(app_id=ctx.app_id, kind="collection", name="docs", version=2,
                                body=docs(fields={"title": {"type": "string"}}),
                                state="published", author="agent:pai"))
        await s.commit()
    assert (await admin_client.get(f"/api/artifacts/{aid}")).status_code == 404


async def test_app_owned_artifacts_are_not_listed_or_counted(
        sf, seed_agent, agent_store, token_client, admin_client):
    ctx = await make_app(sf, [docs()])
    owned = await _owned(sf, ctx, "shared")
    mine = await upload(admin_client, png_bytes())
    ids = [a["id"] for a in (await admin_client.get("/api/artifacts")).json()]
    assert ids == [mine["id"]]
    stats = (await admin_client.get("/api/artifacts/stats")).json()
    assert (stats["count"], stats["bytes"]) == (1, mine["size"])
    bob = await _agent_headers(sf, seed_agent, agent_store, "bob")
    ids = [a["id"] for a in (await token_client.get("/api/artifacts", headers=bob)).json()]
    assert owned not in ids
    stats = (await token_client.get("/api/artifacts/stats", headers=bob)).json()
    assert stats["count"] == 1


async def test_app_owned_artifacts_are_not_renamed_or_deleted_by_the_artifact_routes(
        sf, seed_agent, agent_store, token_client, admin_client):
    ctx = await make_app(sf, [docs()])
    aid = await _owned(sf, ctx, "file")
    secret = await _owned(sf, ctx, "secret")
    r = await admin_client.patch(f"/api/artifacts/{aid}", json={"name": "x"})
    assert r.status_code == 409 and "record" in r.json()["detail"]
    assert (await admin_client.delete(f"/api/artifacts/{aid}")).status_code == 409
    assert (await admin_client.delete(f"/api/artifacts/{secret}")).status_code == 404
    bob = await _agent_headers(sf, seed_agent, agent_store, "bob",
                               grants=(RELAY_GRANT, ARTIFACTS_GRANT,
                                       "mcp__platform__agents_edit"))
    assert (await token_client.delete(f"/api/artifacts/{aid}", headers=bob)).status_code == 404
    assert not await gone(sf, aid)
    async with sf() as s:
        assert (await s.get(Artifact, aid)).deleted_at is None


async def test_app_owned_artifacts_are_not_a_face_or_a_parent(sf, admin_client):
    ctx = await make_app(sf, [docs()])
    aid = await _owned(sf, ctx, "secret")
    r = await admin_client.put("/api/agents/hello-world/image", json={"artifact_id": aid})
    assert r.status_code == 404, r.text
    async with sf() as s:
        assert (await s.get(AgentDef, "hello-world")).image_artifact_id is None
    # Deriving from one is reading it: Kyle can't read `secret`, so it's no parent.
    import base64
    r = await admin_client.post("/api/artifacts", json={
        "name": "d.png", "content_b64": base64.b64encode(png_bytes(8, 8)).decode(),
        "meta": {"parent_id": aid}})
    assert r.status_code == 404, r.text


async def test_an_agent_cannot_wear_an_app_owned_artifact_it_reads(
        sf, seed_agent, agent_store, token_client):
    """The self path too: even the App's owner, who reads it, can't make it
    the face everyone sees."""
    from .test_agent_self import own_run
    ctx = await make_app(sf, [docs()], owner="agent:companion")
    aid = await _owned(sf, ctx, "secret", owner="agent:companion")
    _, headers = await own_run(sf, seed_agent, agent_store, "companion")
    r = await token_client.put("/api/agent-self/avatar", headers=headers,
                               json={"artifact_id": aid})
    assert r.status_code == 404, r.text
    async with sf() as s:
        assert (await s.get(AgentDef, "companion")).image_artifact_id is None


# --- the feed ----------------------------------------------------------------------------

async def test_the_feed_filters_app_owned_events_per_recipient(
        sf, seed_agent, agent_store, token_client, admin_client):
    ctx = await make_app(sf, [docs()])
    file_ = await _owned(sf, ctx, "file")
    shared = await _owned(sf, ctx, "shared")
    ordinary = await upload(admin_client, png_bytes())
    feed = admin_client._transport.app.state.artifacts_feed

    async def view(aid):
        async with sf() as s:
            return store.artifact_view(await s.get(Artifact, aid))

    frames = [{"event": "created", "artifact": await view(file_), "agent": "pai",
               "app": {"app_id": ctx.app_id, "collection": "docs", "field": "file"}},
              {"event": "created", "artifact": await view(shared), "agent": "pai"},
              {"event": "created", "artifact": ordinary, "agent": None}]

    async def received(client, headers=None):
        async with sse(client, "/api/artifacts/events", headers=headers or {}) as (_, stream):
            for frame in frames:
                feed.publish(STREAM, "artifact", frame)
            got = []
            while len(got) < 3:
                event, data, _ = await stream.event()
                got.append(data["artifact"]["id"])
                if data["artifact"]["id"] == ordinary["id"]:
                    break
            return got

    bob = await _agent_headers(sf, seed_agent, agent_store, "bob")
    assert await received(token_client, bob) == [shared, ordinary["id"]]
    assert await received(admin_client) == [file_, shared, ordinary["id"]]


async def test_the_feed_drops_an_app_event_whose_artifact_is_gone(sf, admin_client):
    """The event outlived its artifact (the record was deleted in between):
    with no owning field left to authorize it, nobody gets it."""
    ctx = await make_app(sf, [docs()])
    ordinary = await upload(admin_client, png_bytes())
    feed = admin_client._transport.app.state.artifacts_feed
    ghost = {"id": uuid.uuid4().hex, "name": "secret-plans.png"}
    async with sse(admin_client, "/api/artifacts/events") as (_, stream):
        feed.publish(STREAM, "artifact", {"event": "created", "artifact": ghost,
                                          "app": {"app_id": ctx.app_id, "collection": "docs",
                                                  "field": "file"}})
        feed.publish(STREAM, "artifact", {"event": "created", "artifact": ordinary})
        _, data, _ = await stream.event()
    assert data["artifact"]["id"] == ordinary["id"]


# --- ordinary artifacts ------------------------------------------------------------------

async def test_ordinary_artifacts_are_unaffected(sf, seed_agent, agent_store, token_client,
                                                 admin_client):
    ctx = await make_app(sf, [docs()])
    await _owned(sf, ctx, "file")
    a = await upload(admin_client, png_bytes())
    bob = await _agent_headers(sf, seed_agent, agent_store, "bob")
    bare = await _agent_headers(sf, seed_agent, agent_store, "bare", grants=(RELAY_GRANT,))
    reader = await _human_token(sf, "someone", "reader")
    for path in _routes(a["id"]).values():
        if path.endswith("/resource"):
            continue
        assert (await token_client.get(path, headers=bob)).status_code == 200
        assert (await token_client.get(path, headers=reader)).status_code == 200
        assert (await token_client.get(path, headers=bare)).status_code == 403
    assert (await admin_client.get(f"/api/artifacts/{a['id']}/resource")).status_code == 200
    assert (await token_client.get(f"/api/artifacts/{a['id']}/resource",
                                   headers=bob)).status_code == 404
    # An unknown id is still the grant's 403 for an agent without it.
    assert (await token_client.get(f"/api/artifacts/{uuid.uuid4().hex}",
                                   headers=bare)).status_code == 403
    r = await admin_client.patch(f"/api/artifacts/{a['id']}", json={"name": "renamed"})
    assert r.status_code == 200 and r.json()["name"] == "renamed"
    assert (await admin_client.delete(f"/api/artifacts/{a['id']}")).status_code == 200
