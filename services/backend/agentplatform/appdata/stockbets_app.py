"""The reviewed `stock-bets` state App and its one-shot creation.

Kyle asked for the App on 2026-11-20 (ENG-16, wiki `stock-bets`). It is new,
so there is no data to copy: this records the approved bundle, owned by
`olu`, exactly as `running_cutover` does for a migrated App. A fresh install
creates no Apps; run it once in the API pod:

    python -m agentplatform.appdata.stockbets_app          # dry run
    python -m agentplatform.appdata.stockbets_app --apply

Every price and result field on a bet is tool-only (`writers`), so an agent
holding the collection still cannot type a number: only `tools/stock_bets`,
which fetches the bars itself, writes them.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select

from agentplatform.appdata.definitions import validate_app

NAME = "stock-bets"
OWNER = "olu"
PET = "agent:stock-bets"
TOOL = "tool:stock_bets"
SYMBOLS = ("SPY QQQ IWM DIA XLK XLF XLE XLV XLY XLI XLU XLP SMH TLT GLD USO "
           "AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO AMD").split()


def _n(**kw) -> dict:
    return {"type": "number", **kw}


def bundle() -> dict:
    readers = ["owner", "kyle", PET, "agent:pai"]
    bets = {
        "collection": "bets",
        "description": "One paper bet per US session, scored by the stock_bets tool "
                       "from Yahoo daily bars. Paper only.",
        "fields": {
            "session": {"type": "date", "required": True, "label": "Session"},
            "exit_session": {"type": "date", "required": True, "label": "Exit session"},
            "symbol": {"type": "enum", "values": SYMBOLS, "required": True},
            "side": {"type": "enum", "values": ["long", "short"], "required": True},
            "exit_kind": {"type": "enum", "values": ["open", "close", "bracket"],
                          "required": True, "label": "Exit plan"},
            "target_pct": _n(min=0.2, max=5, label="Target %"),
            "stop_pct": _n(min=0.2, max=5, label="Stop %"),
            "confidence": _n(min=0.5, max=0.9, required=True),
            "basis": {"type": "enum", "required": True,
                      "values": ["catalyst", "momentum", "reversal", "macro", "default"]},
            "thesis": {"type": "text", "max": 600, "required": True},
            "sources": {"type": "list", "items": {"type": "string", "max": 300},
                        "max_items": 10},
            "playbook": {"type": "string", "max": 16, "required": True},
            "taken_at": {"type": "datetime", "required": True, "label": "Taken at"},
            "late": {"type": "bool", "required": True},
            "origin": {"type": "enum", "values": ["pet", "import"], "required": True},
            "status": {"type": "enum", "values": ["open", "scored"], "required": True},
            "entry": _n(min=0, label="Entry"),
            "exit": _n(min=0, label="Exit"),
            "exit_reason": {"type": "enum", "label": "Exit reason",
                            "values": ["open", "close", "target", "stop",
                                       "gap_target", "gap_stop"]},
            "gross_pct": _n(label="Gross %"),
            "net_pct": _n(label="Net %"),
            "pnl_usd": _n(label="P&L $"),
            "win": {"type": "bool", "label": "Win"},
            "win_pct": {"type": "int", "min": 0, "max": 100},
            "direction_hit": {"type": "bool", "label": "Direction hit"},
            "direction_pct": {"type": "int", "min": 0, "max": 100},
            "spy_pct": _n(label="SPY %"),
            "spy_net_pct": _n(label="SPY net %"),
            "spy_pnl_usd": _n(label="SPY P&L $"),
            "edge_pct": _n(label="Edge vs SPY %"),
            "field_net_pct": _n(label="Random-pick net %"),
            "field_n": {"type": "int", "min": 0, "max": 25},
            "bar_open": _n(min=0), "bar_high": _n(min=0),
            "bar_low": _n(min=0), "bar_close": _n(min=0),
            "pet_net_pct": _n(label="Pet's own net % (imported)"),
            "scored_at": {"type": "datetime"},
            "lesson": {"type": "text", "max": 500},
        },
        "access": {"read": readers, "create": ["owner", PET],
                   "update": ["owner", PET], "delete": ["kyle"]},
        "writers": {"create": [TOOL], "update": [TOOL]},
        "rules": [{"kind": "unique", "fields": ["session"]},
                  {"kind": "immutable_after_create",
                   "fields": ["session", "exit_session", "symbol", "side", "exit_kind",
                              "target_pct", "stop_pct", "confidence", "basis", "thesis",
                              "sources", "playbook", "taken_at", "late", "origin"]}],
        "indexed": ["status", "playbook", "session"],
    }
    notes = {"read": ["owner", "kyle", PET], "create": ["owner"], "update": [],
             "delete": ["kyle"]}
    playbook = {
        "collection": "playbook",
        "description": "PLAYBOOK versions Olu tunes; each bet records the one in force.",
        "fields": {
            "name": {"type": "string", "max": 16, "required": True, "label": "Version"},
            "starts": {"type": "date", "required": True},
            "text": {"type": "text", "max": 16000, "required": True},
            "reason": {"type": "text", "max": 4000, "required": True},
            "prediction": {"type": "text", "max": 4000, "required": True},
            "refuter": {"type": "text", "max": 4000, "required": True},
        },
        "write_mode": "immutable", "access": notes,
        "writers": {"create": [TOOL]},
        "rules": [{"kind": "unique", "fields": ["name"]}],
        "indexed": ["name", "starts"],
    }
    research = {
        "collection": "research",
        "description": "How other trading agents work: what Olu read or tested.",
        "fields": {
            "day": {"type": "date", "required": True},
            "source": {"type": "string", "max": 1000, "required": True},
            "claim": {"type": "text", "max": 4000, "required": True},
            "status": {"type": "enum", "values": ["READ", "TESTED"], "required": True},
            "idea": {"type": "text", "max": 4000, "required": True},
        },
        "write_mode": "immutable", "access": notes,
        "writers": {"create": [TOOL]},
        "indexed": ["status", "day"],
    }
    scored = {"field": "status", "op": "eq", "value": "scored"}
    measures = [
        {"fn": "count", "as": "bets"},
        {"fn": "avg", "field": "win_pct", "as": "win_rate_pct"},
        {"fn": "avg", "field": "direction_pct", "as": "direction_pct"},
        {"fn": "avg", "field": "net_pct", "as": "avg_net_pct"},
        {"fn": "sum", "field": "pnl_usd", "as": "pnl_usd"},
    ]
    views = [
        {"view": "bets_all", "collection": "bets",
         "sort": [{"field": "session", "dir": "asc"}], "limit": 200, "paging": True},
        {"view": "playbook_all", "collection": "playbook",
         "sort": [{"field": "starts", "dir": "asc"}], "limit": 200, "paging": True},
        {"view": "research_all", "collection": "research",
         "sort": [{"field": "day", "dir": "desc"}], "limit": 200, "paging": True},
        {"view": "bets_recent", "collection": "bets",
         "sort": [{"field": "session", "dir": "desc"}], "limit": 60, "paging": True},
        {"view": "open_bets", "collection": "bets",
         "filter": [{"field": "status", "op": "eq", "value": "open"}],
         "sort": [{"field": "session", "dir": "desc"}], "limit": 5},
        {"view": "scored_count", "collection": "bets", "filter": [scored],
         "aggregates": [{"fn": "count", "as": "n"}]},
        {"view": "win_rate", "collection": "bets", "filter": [scored],
         "aggregates": [{"fn": "avg", "field": "win_pct", "as": "value"}]},
        {"view": "pnl_total", "collection": "bets", "filter": [scored],
         "aggregates": [{"fn": "sum", "field": "pnl_usd", "as": "value"}]},
        {"view": "spy_pnl_total", "collection": "bets", "filter": [scored],
         "aggregates": [{"fn": "sum", "field": "spy_pnl_usd", "as": "value"}]},
        {"view": "net_by_session", "collection": "bets", "filter": [scored],
         "group_by": {"field": "session", "bucket": "day"},
         "aggregates": [{"fn": "sum", "field": "net_pct", "as": "net_pct"}],
         "limit": 200},
        {"view": "by_playbook", "collection": "bets", "filter": [scored],
         "group_by": {"field": "playbook"}, "aggregates": measures, "limit": 50},
        {"view": "by_basis", "collection": "bets", "filter": [scored],
         "group_by": {"field": "basis"}, "aggregates": measures, "limit": 10},
        {"view": "by_side", "collection": "bets", "filter": [scored],
         "group_by": {"field": "side"}, "aggregates": measures, "limit": 2},
        {"view": "baselines", "collection": "bets", "filter": [scored],
         "group_by": {"field": "status"},
         "aggregates": [
             {"fn": "avg", "field": "net_pct", "as": "pet_avg_net_pct"},
             {"fn": "avg", "field": "spy_net_pct", "as": "spy_avg_net_pct"},
             {"fn": "avg", "field": "field_net_pct", "as": "field_avg_net_pct"},
             {"fn": "sum", "field": "pnl_usd", "as": "pet_pnl_usd"},
             {"fn": "sum", "field": "spy_pnl_usd", "as": "spy_pnl_usd"}],
         "limit": 1},
        {"view": "playbook_versions", "collection": "playbook",
         "sort": [{"field": "starts", "dir": "desc"}], "limit": 50},
        {"view": "research_recent", "collection": "research",
         "sort": [{"field": "day", "dir": "desc"}], "limit": 30},
    ]
    layout = (
        '<section class="ap-stack">'
        '<header class="ap-hero"><h1>Stock bets</h1>'
        '<p class="ap-muted">Olu\'s pet makes one paper bet each US trading day. '
        'The App stamps the time, fetches the bars and scores every bet; no agent '
        'writes a price or a result.</p></header>'
        '<div class="ap-grid">'
        '<div class="ap-card"><ap-view name="settled"></ap-view></div>'
        '<div class="ap-card"><ap-view name="winrate"></ap-view></div>'
        '<div class="ap-card"><ap-view name="pnl"></ap-view></div>'
        '<div class="ap-card"><ap-view name="spypnl"></ap-view></div>'
        '</div>'
        '<ap-view name="open"></ap-view>'
        '<ap-view name="baselines"></ap-view>'
        '<div class="ap-card"><ap-view name="chart"></ap-view></div>'
        '<ap-view name="bets"></ap-view>'
        '<div class="ap-grid">'
        '<div class="ap-card"><ap-view name="playbooks"></ap-view></div>'
        '<div class="ap-card"><ap-view name="bases"></ap-view></div>'
        '</div>'
        '<ap-view name="versions"></ap-view>'
        '<ap-view name="research"></ap-view>'
        '<ap-view name="noise"></ap-view>'
        '</section>')
    group_cols = [{"field": "bets", "label": "Bets", "format": "number"},
                  {"field": "win_rate_pct", "label": "Win %", "format": "number"},
                  {"field": "direction_pct", "label": "Direction %", "format": "number"},
                  {"field": "avg_net_pct", "label": "Avg net %", "format": "number"},
                  {"field": "pnl_usd", "label": "P&L $", "format": "number"}]
    page = {
        "page": "home", "title": "Stock bets", "layout": layout,
        "blocks": [
            {"kind": "metric", "slot": "settled", "label": "Settled bets",
             "view": "scored_count"},
            {"kind": "metric", "slot": "winrate", "label": "Win rate %", "view": "win_rate"},
            {"kind": "metric", "slot": "pnl", "label": "Paper P&L $", "view": "pnl_total"},
            {"kind": "metric", "slot": "spypnl", "label": "Always-long SPY P&L $",
             "view": "spy_pnl_total"},
            {"kind": "table", "slot": "open", "view": "open_bets",
             "title": "Waiting to be scored",
             "columns": [{"field": "session", "format": "date"}, {"field": "symbol"},
                         {"field": "side"}, {"field": "exit_kind"},
                         {"field": "confidence", "format": "number"},
                         {"field": "thesis"}]},
            {"kind": "stat_row", "slot": "baselines", "view": "baselines",
             "title": "Pet vs baselines (average net % per bet)",
             "columns": [{"field": "pet_avg_net_pct", "label": "Pet", "format": "number"},
                         {"field": "spy_avg_net_pct", "label": "Always-long SPY",
                          "format": "number"},
                         {"field": "field_avg_net_pct", "label": "Random pick",
                          "format": "number"}]},
            {"kind": "chart", "slot": "chart", "view": "net_by_session",
             "title": "Net % per bet", "x": "session", "y": "net_pct", "unit": "%"},
            {"kind": "table", "slot": "bets", "view": "bets_recent", "title": "Every bet",
             "columns": [{"field": "session", "format": "date"}, {"field": "symbol"},
                         {"field": "side"}, {"field": "exit_kind"},
                         {"field": "playbook"}, {"field": "exit_reason"},
                         {"field": "net_pct", "format": "number"},
                         {"field": "win", "format": "bool"},
                         {"field": "spy_net_pct", "format": "number"},
                         {"field": "late", "format": "bool"},
                         {"field": "thesis"}, {"field": "lesson"}]},
            {"kind": "table", "slot": "playbooks", "view": "by_playbook",
             "title": "By playbook version",
             "columns": [{"field": "playbook", "label": "Version"}, *group_cols]},
            {"kind": "table", "slot": "bases", "view": "by_basis", "title": "By basis",
             "columns": [{"field": "basis", "label": "Basis"}, *group_cols]},
            {"kind": "table", "slot": "versions", "view": "playbook_versions",
             "title": "Playbook versions (where each starts)",
             "columns": [{"field": "name"}, {"field": "starts", "format": "date"},
                         {"field": "reason"}, {"field": "prediction"},
                         {"field": "refuter"}]},
            {"kind": "table", "slot": "research", "view": "research_recent",
             "title": "Research: how other trading agents work",
             "columns": [{"field": "day", "format": "date"}, {"field": "status"},
                         {"field": "source"}, {"field": "claim"}, {"field": "idea"}]},
            {"kind": "text", "slot": "noise",
             "text": "Win = net % above zero after a 0.05% cost on a $1,000 paper "
                     "stake. Direction % ignores the exit plan, so a rising win rate "
                     "without a rising direction % came from the exit plan, not from "
                     "calling direction. Random pick is the same side and exit plan on "
                     "all 25 symbols, averaged. Over 20 bets luck alone moves the win "
                     "rate about 11 points; about 150 bets are needed to tell 60% "
                     "from 50%."},
        ]}
    return {
        "collections": [bets, playbook, research],
        "views": views,
        "pages": [page],
        "app_tools": [{"tool": "stock_bets", "roles": {
            "bets": {"collection": "bets", "verbs": ["read", "create", "update"]},
            "playbook": {"collection": "playbook", "verbs": ["read", "create"]},
            "research": {"collection": "research", "verbs": ["read", "create"]},
        }}],
    }


async def create(*, apply: bool = False) -> dict:
    from agentplatform.appdata.models import AppDataApp, AppDataDefinition
    from agentplatform.config import Settings
    from agentplatform.db import make_engine, make_session_factory

    definitions = bundle()
    validate_app(definitions)
    counts = {k: len(v) for k, v in definitions.items()}
    engine = make_engine(Settings().db_url)
    try:
        async with make_session_factory(engine)() as session:
            async with session.begin():
                existing = (await session.execute(select(AppDataApp).where(
                    AppDataApp.name == NAME))).scalar_one_or_none()
                if existing is not None:
                    return {"exists": True, "app_id": existing.id,
                            "approved_version": existing.approved_version}
                if not apply:
                    return {"dry_run": True, "definitions": counts}
                app = AppDataApp(name=NAME, owner_kind="agent", owner_id=OWNER,
                                 timezone="America/Toronto",
                                 description="Olu's pet makes one paper stock bet per US "
                                             "session; the App scores it.",
                                 approved_version=1, authority_generation=1)
                session.add(app)
                await session.flush()
                for kind, plural in (("collection", "collections"), ("view", "views"),
                                     ("page", "pages"), ("tool", "app_tools")):
                    for body in definitions[plural]:
                        session.add(AppDataDefinition(
                            app_id=app.id, kind=kind, name=body[kind], state="published",
                            version=1, revision=1, body=body, author="recipe:stock-bets",
                            approved_by="kyle",
                            reason="Kyle's stock-bets request, 2026-11-20 (ENG-16)"))
                await session.flush()
                return {"created": True, "app_id": app.id, "definitions": counts}
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="create the App")
    print(json.dumps(asyncio.run(create(apply=parser.parse_args().apply)), sort_keys=True))


if __name__ == "__main__":
    main()
