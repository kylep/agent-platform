"""Reports: git-declared types, sanitized instances, charts, retention
(docs/design/11)."""
from datetime import timedelta

import pytest

from agentplatform.db import ApiKey, Report, utcnow
from agentplatform.apikeys import generate_token, hash_token, token_prefix
from agentplatform.pruning import ReportPruner
from agentplatform.reportcharts import ChartSpec, render_chart
from agentplatform.reportregistry import ReportTypeRegistry
from agentplatform.reportsanitize import sanitize_report_html
from tests.conftest import REPO_REPORTS


# --- registry ----------------------------------------------------------------

def test_repo_registry_loads_shipped_types():
    reg = ReportTypeRegistry(REPO_REPORTS)
    names = {i.name for i in reg.list()}
    assert "daily-news" in names
    dn = reg.get("daily-news").spec
    assert dn.generator == "news" and dn.cadence == "daily" and dn.retention_days == 365
    assert all(i.spec is not None and i.error is None for i in reg.list())


def test_registry_surfaces_broken_yaml(tmp_path):
    d = tmp_path / "busted"; d.mkdir()
    (d / "report.yaml").write_text("cadence: hourly\n")
    info = ReportTypeRegistry(tmp_path).get("busted")
    assert info.spec is None and "cadence" in info.error


# --- sanitization ------------------------------------------------------------

def test_sanitize_strips_scripts_styles_and_foreign_classes():
    dirty = ('<section class="rk-section bad-class"><h2>ok</h2>'
             '<script>alert(1)</script><div style="color:red" onclick="x()">t</div>'
             '<a href="javascript:alert(1)">bad</a><a href="https://x.com">good</a></section>')
    clean = sanitize_report_html(dirty)
    assert "<script" not in clean and "onclick" not in clean and "style=" not in clean
    assert 'class="rk-section"' in clean and "bad-class" not in clean
    assert "javascript:" not in clean and 'href="https://x.com"' in clean
    assert 'rel="noopener noreferrer"' in clean


def test_sanitize_keeps_chart_svg_but_blocks_paint_urls():
    svg = render_chart(ChartSpec(kind="bar", series=[{"values": [1, 2, 3]}],
                                 labels=["a", "b", "c"], title="t"))
    clean = sanitize_report_html(svg)
    assert "<svg" in clean and "var(--ds-chart-1)" in clean and "<rect" in clean
    hostile = '<svg><rect fill="url(#evil)" width="5" height="5"/></svg>'
    assert "url(" not in sanitize_report_html(hostile)


# --- charts ------------------------------------------------------------------

def test_chart_kinds_render_and_validate():
    for kind in ("bar", "line", "sparkline", "donut"):
        svg = render_chart(ChartSpec(kind=kind, series=[{"values": [3, 1, 4, 1, 5]}],
                                     labels=["a", "b", "c", "d", "e"]))
        assert svg.startswith("<svg") and "var(--ds-chart-" in svg
    with pytest.raises(ValueError):
        ChartSpec(kind="pie", series=[{"values": [1]}])
    with pytest.raises(ValueError):
        ChartSpec(kind="donut", series=[{"values": [1]}, {"values": [2]}])


def test_chart_escapes_labels():
    svg = render_chart(ChartSpec(kind="bar", series=[{"values": [1]}],
                                 labels=['<img onerror=x>'], title='<b>&'))
    assert "<img" not in svg and "&lt;b&gt;&amp;" in svg


# --- the API loop ------------------------------------------------------------

async def test_save_list_get_replace_delete(admin_client):
    body = {"type": "daily-news", "date": "2026-08-03", "title": "News",
            "html": '<section class="rk-section"><p>hello</p></section>'}
    r = await admin_client.post("/api/reports", json=body)
    assert r.status_code == 201 and r.json()["replaced"] is False
    rid = r.json()["id"]
    # same identity → replaced, same row
    r = await admin_client.post("/api/reports", json={**body, "html": "<p>v2</p>"})
    assert r.json()["replaced"] is True and r.json()["id"] == rid
    r = await admin_client.get("/api/reports?type=daily-news")
    assert [x["id"] for x in r.json()] == [rid]
    r = await admin_client.get(f"/api/reports/{rid}")
    assert r.json()["html"] == "<p>v2</p>"
    # type listing shows the instance count
    r = await admin_client.get("/api/report-types")
    dn = next(t for t in r.json() if t["name"] == "daily-news")
    assert dn["count"] == 1 and dn["latest_date"] == "2026-08-03"
    assert (await admin_client.delete(f"/api/reports/{rid}")).status_code == 200
    assert (await admin_client.get(f"/api/reports/{rid}")).status_code == 404


async def test_save_validates_type_date_and_cadence(admin_client):
    ok = '<p>x</p>'
    r = await admin_client.post("/api/reports", json={
        "type": "ghost", "date": "2026-08-03", "html": ok})
    assert r.status_code == 422 and "declare" in r.json()["detail"]
    r = await admin_client.post("/api/reports", json={
        "type": "daily-news", "date": "08/03/2026", "html": ok})
    assert r.status_code == 422
    # daily cadence refuses a time; garbage time refused
    r = await admin_client.post("/api/reports", json={
        "type": "daily-news", "date": "2026-08-03", "time": "09-00", "html": ok})
    assert r.status_code == 422 and "no time" in r.json()["detail"]


async def test_agent_key_may_only_write_its_generated_type(client, sf):
    await client.post("/api/setup", json={"password": "pw12345678"})
    async def mint(agent):
        token = generate_token()
        async with sf() as s:
            s.add(ApiKey(name=f"invoke:{agent}", role="annotator", agent=agent,
                         key_hash=hash_token(token), prefix=token_prefix(token)))
            await s.commit()
        return {"Authorization": f"Bearer {token}"}
    body = {"type": "daily-news", "date": "2026-08-03", "html": "<p>x</p>"}
    r = await client.post("/api/reports", json=body, headers=await mint("pai"))
    assert r.status_code == 403 and "generated by" in r.json()["detail"]
    r = await client.post("/api/reports", json=body, headers=await mint("news"))
    assert r.status_code == 201


async def test_chart_endpoint(admin_client):
    r = await admin_client.post("/api/report-kit/chart", json={
        "kind": "sparkline", "series": [{"values": [1, 2, 1, 3]}],
        "width": 120, "height": 32})
    assert r.status_code == 200 and r.json()["svg"].startswith("<svg")
    r = await admin_client.post("/api/report-kit/chart", json={
        "kind": "pie", "series": [{"values": [1]}]})
    assert r.status_code == 422


# --- retention ---------------------------------------------------------------

async def test_report_pruner_respects_per_type_retention(sf, tmp_path):
    d = tmp_path / "ephemeral"; d.mkdir()
    (d / "report.yaml").write_text("retention_days: 7\n")
    k = tmp_path / "keeper"; k.mkdir()
    (k / "report.yaml").write_text("retention_days: 0\n")
    now = utcnow()
    old = (now - timedelta(days=10)).date().isoformat()
    async with sf() as s:
        s.add(Report(type="ephemeral", date=old, html="x"))
        s.add(Report(type="ephemeral", date=now.date().isoformat(), html="x"))
        s.add(Report(type="keeper", date=old, html="x"))
        await s.commit()
    deleted = await ReportPruner(sf, ReportTypeRegistry(tmp_path)).prune_once(now=now)
    assert deleted == 1
    async with sf() as s:
        from sqlalchemy import select
        left = (await s.execute(select(Report.type, Report.date))).all()
    assert ("ephemeral", old) not in left and len(left) == 2
