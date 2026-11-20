"""The stock-bets App: only the stock_bets tool writes a bet or its numbers."""
import pytest

from agentplatform.appdata import lifecycle as L
from agentplatform.appdata.definitions import validate_app
from agentplatform.appdata.access import RecordError as AppDataError
from agentplatform.appdata.models import AppDataApp, AppDataDefinition
from agentplatform.appdata.stockbets_app import bundle
from agentplatform.db import Base, make_engine, make_session_factory

APP_ID = "b" * 32
BET = {"session": "2026-11-20", "exit_session": "2026-11-23", "symbol": "NVDA",
       "side": "long", "exit_kind": "close", "confidence": 0.55, "basis": "catalyst",
       "thesis": "t", "playbook": "v1", "taken_at": "2026-11-20T20:30:00+00:00",
       "late": False, "origin": "pet", "status": "open"}
TOOL = L.Actor("agent:stock-bets", via_tool="tool:stock_bets")


def test_bundle_validates_with_tool_only_numbers():
    app = validate_app(bundle())
    bets = app.collections["bets"]
    assert bets.writers.create == ["tool:stock_bets"]
    assert bets.writers.update == ["tool:stock_bets"]
    assert set(app.app_tools) == {"stock_bets"}
    assert "home" in app.pages


@pytest.fixture
async def sf(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/bets.db")
    tables = [t for n, t in Base.metadata.tables.items() if n.startswith("app_data_")]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    factory = make_session_factory(engine)
    definitions = bundle()
    async with factory() as s:
        s.add(AppDataApp(id=APP_ID, name="stock-bets", owner_kind="agent",
                         owner_id="olu", approved_version=1,
                         timezone="America/Toronto"))
        for kind, plural in (("collection", "collections"), ("view", "views"),
                             ("page", "pages"), ("tool", "app_tools")):
            for body in definitions[plural]:
                s.add(AppDataDefinition(app_id=APP_ID, kind=kind, name=body[kind],
                                        version=1, body=body, state="published",
                                        author="recipe:stock-bets"))
        await s.commit()
    yield factory
    await engine.dispose()


async def _create(sf, actor, values, rid):
    async with sf() as s:
        out = await L.record_create(s, actor, APP_ID, request_id=rid,
                                    collection="bets", values=values)
        await s.commit()
        return out


async def test_agent_cannot_write_a_bet_or_a_price_directly(sf):
    for principal in ("agent:stock-bets", "agent:olu"):
        with pytest.raises(AppDataError) as refused:
            await _create(sf, L.Actor(principal), {**BET, "net_pct": 50.0},
                          "direct-" + principal)
        assert refused.value.code == "AD-TOOL-ONLY", refused.value.code
    receipt = await _create(sf, TOOL, BET, "tool-1")
    with pytest.raises(AppDataError) as refused:
        async with sf() as s:
            await L.record_update(s, L.Actor("agent:stock-bets"), APP_ID,
                                  request_id="direct-score", collection="bets",
                                  record_id=receipt["id"], values={"net_pct": 9.9},
                                  expected_version=receipt["version"])
    assert refused.value.code == "AD-TOOL-ONLY", refused.value.code


async def test_second_bet_for_a_session_is_refused(sf):
    await _create(sf, TOOL, BET, "tool-1")
    with pytest.raises(AppDataError) as refused:
        await _create(sf, TOOL, {**BET, "symbol": "SPY"}, "tool-2")
    assert refused.value.code == "AD-UNIQUE", refused.value.code


async def test_bet_terms_are_frozen_after_create(sf):
    receipt = await _create(sf, TOOL, BET, "tool-1")
    with pytest.raises(AppDataError) as refused:
        async with sf() as s:
            await L.record_update(s, TOOL, APP_ID, request_id="tool-edit",
                                  collection="bets", record_id=receipt["id"],
                                  values={"symbol": "SPY"},
                                  expected_version=receipt["version"])
    assert refused.value.code == "AD-RULE-IMMUTABLE-FIELD", refused.value.code
    async with sf() as s:
        out = await L.record_update(s, TOOL, APP_ID, request_id="tool-score",
                                    collection="bets", record_id=receipt["id"],
                                    values={"status": "scored", "net_pct": 2.45},
                                    expected_version=receipt["version"])
        await s.commit()
    assert out["version"] == receipt["version"] + 1
