"""The seams between the DB-first agent pieces (docs/design/15).

Every task in the migration tested its own half. These tests cover the joins —
the places where one component's output is another's input, and where a
regression would pass both suites either side of it:

  * a grant written through the API becomes a token's authority (`/api/whoami`
    is what the broker asks, so this is the whole grant enforcement chain);
  * the change log's `changed_via` sequence across a definition's whole life,
    which is the audit trail the "no PR review any more" trade rests on;
  * launcher -> agentdef endpoint -> runner, driven by a REAL row rather than
    fixtures at each end, so the three services cannot drift apart on what a
    grant means;
  * the store's background refresh is cancelled at shutdown.
"""
import asyncio
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

from agentplatform.agents import AgentStore
from agentplatform.config import Settings
from agentplatform.db import AgentDef, Run, RunState
from agentplatform.joblauncher import K8sJobLauncher
from tests.conftest import (REPO_APPS, REPO_REPORTS, REPO_SECRETS, REPO_SKILLS,
                            REPO_TOOLS)
from tests.test_agents_api import a_def, bearer, versions_of

AGENTS_GRANT = "mcp__platform__agents_grant"
MEMORY = "mcp__platform__memory"
RUNS_READ = "mcp__platform__runs_read"


def app_over(sf, producer, secret_store, store, tmp_path):
    """The `client` fixture's app, built inline. These tests need TWO callers
    against one app (an admin session and an agent bearer), and the shared
    fixture yields a single client whose login cookie would authenticate every
    request as admin — quietly turning a tool-authority test into a test of the
    admin path."""
    from agentplatform.api.app import create_app
    checkout_root = tmp_path / "checkout"
    checkout_root.mkdir(exist_ok=True)
    return create_app(Settings(checkout_root=str(checkout_root),
                               secrets_root=str(REPO_SECRETS),
                               skills_root=str(REPO_SKILLS),
                               reports_root=str(REPO_REPORTS),
                               apps_root=str(REPO_APPS),
                               tools_root=str(REPO_TOOLS)), sf, producer,
                      secret_store=secret_store, agent_store=store)


def caller(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://t")


@pytest.fixture
async def two_callers(sf, producer, secret_store, agent_store, tmp_path):
    """(admin session, cookie-less caller) over one app."""
    app = app_over(sf, producer, secret_store, agent_store, tmp_path)
    async with caller(app) as admin, caller(app) as anon:
        await admin.post("/api/setup", json={"password": "pw12345678"})
        await admin.post("/api/login", json={"password": "pw12345678"})
        yield admin, anon


# --- seam 1: a grant is written, and a token's authority changes -------------

async def test_a_grant_written_through_the_api_reaches_the_agents_own_token(
        two_callers, sf, seed_agent, agent_store):
    """The whole grant chain, end to end, with no step stubbed.

    `agents_grant` exists so a definition's capabilities can be changed without
    a human, and `/api/whoami` is what the MCP broker asks before it lets a
    tool call through. Those are the two ends of one mechanism, and each was
    tested against its own fixtures: T3/T4 asserted the write lands in the row,
    T2 asserted whoami reads the row. Nothing asserted that the row a grant
    tool writes is the row whoami reads — so a rename, a normalization, or a
    cache that failed to reload would leave both suites green while a granted
    tool stayed forbidden (or, worse, a revoked one stayed allowed).
    """
    admin_client, client = two_callers
    # A granter whose ONLY authority is the grant tool, and a target with none.
    await seed_agent("granter", platform_tools=[AGENTS_GRANT])
    await agent_store.reload()
    granter = await bearer(sf, "granter")

    created = await admin_client.post("/api/agents", json=a_def("worker"))
    assert created.status_code == 201 and created.json()["platform_tools"] == []

    worker_token = await bearer(sf, "worker")
    before = await client.get("/api/whoami", headers=worker_token)
    assert before.status_code == 200 and before.json()["tools"] == []

    # GRANT. The granter sends the whole definition (PUT replaces), changing
    # only a grant field — which is exactly the authority it holds.
    granted = await client.put("/api/agents/worker",
                               json=a_def("worker", platform_tools=[MEMORY]),
                               headers=granter)
    assert granted.status_code == 200, granted.text
    assert granted.json()["platform_tools"] == [MEMORY]

    after = await client.get("/api/whoami", headers=worker_token)
    assert after.json()["tools"] == [MEMORY]
    assert after.json()["agent"] == "worker"

    # REVOKE. The same authority in the other direction; the token must lose it.
    revoked = await client.put("/api/agents/worker", json=a_def("worker"),
                               headers=granter)
    assert revoked.status_code == 200
    assert (await client.get("/api/whoami", headers=worker_token)).json()["tools"] == []

    # And the trail says who did both, without either call naming a principal.
    log = await versions_of(sf, "worker")
    assert [(v.version, v.changed_via) for v in log] == [
        (1, "admin"), (2, "tool:agents_grant"), (3, "tool:agents_grant")]
    assert log[1].changed_by.endswith("granter")


# --- seam 2: the change log is complete over a definition's whole life -------

async def test_the_change_log_covers_a_definitions_whole_life(two_callers, sf,
                                                              seed_agent,
                                                              agent_store):
    """Create, edit, grant, roll back, delete — one agent, one log.

    Each write path pinned its own `changed_via` label in isolation. What no
    single task could check is that the SEQUENCE is gapless and the versions
    monotonic across the paths together: a write that forgets to log leaves no
    failing test behind it, only a hole in the history. Since design-15 traded
    PR review for "the audit trail is the control", a hole in the history is
    the failure that matters most.
    """
    admin_client, client = two_callers
    await seed_agent("granter", platform_tools=[AGENTS_GRANT])
    await agent_store.reload()
    granter = await bearer(sf, "granter")

    assert (await admin_client.post("/api/agents", json=a_def(
        "shortlived", description="v1"))).status_code == 201
    assert (await admin_client.put("/api/agents/shortlived", json=a_def(
        "shortlived", description="v2"))).status_code == 200
    assert (await client.put("/api/agents/shortlived", json=a_def(
        "shortlived", description="v2", platform_tools=[RUNS_READ]),
        headers=granter)).status_code == 200
    # A no-op is not a change: re-sending v3 verbatim must not pad the log.
    assert (await admin_client.put("/api/agents/shortlived", json=a_def(
        "shortlived", description="v2", platform_tools=[RUNS_READ]))).status_code == 200
    rolled = await admin_client.post("/api/agents/shortlived/rollback/1")
    assert rolled.status_code == 200 and rolled.json()["description"] == "v1"
    assert (await admin_client.delete("/api/agents/shortlived")).status_code == 200

    log = await versions_of(sf, "shortlived")
    assert [v.version for v in log] == [1, 2, 3, 4, 5]         # gapless, monotonic
    assert [v.changed_via for v in log] == [
        "admin", "admin", "tool:agents_grant", "rollback", "delete:admin"]

    # The log outlives the row, and the tombstone alone can rebuild the agent.
    async with sf() as s:
        assert await s.get(AgentDef, "shortlived") is None
    tombstone = log[-1].snapshot
    assert tombstone["description"] == "v1"                    # as it stood at delete
    assert tombstone["platform_tools"] == []                   # the rollback undid it
    reborn = await admin_client.post("/api/agents",
                                     json={**tombstone, "name": "shortlived"})
    assert reborn.status_code == 201


# --- seam 3: launcher -> agentdef endpoint -> runner, over one real row ------

def _load_runner():
    """The runner module, imported by path. It is a script in another service,
    so there is no package to import — but running the chain against the real
    `_render_agent_md` / `_agent_tools` / `_permission_args` is the entire
    point of this test. The alternative (asserting a payload shape here and a
    rendering there) is what let the two drift in the first place."""
    path = Path(__file__).resolve().parents[3] / "services" / "runner" / "runner.py"
    spec = importlib.util.spec_from_file_location("_seam_runner", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_seam_runner"] = mod
    spec.loader.exec_module(mod)
    return mod


async def test_a_rows_grants_survive_the_launcher_the_api_and_the_runner(
        sf, producer, secret_store, tmp_path, monkeypatch):
    """One row, three services, no fixture in between.

    The launcher mints the pod's session token and puts it in the env; the pod
    asks the API for its own definition; the runner renders that into
    `~/.claude/agents/<name>.md` and parses it back out into `--allowedTools`.
    Every hop had a test against a hand-written payload at its own boundary,
    which is exactly the arrangement in which a field rename passes everywhere
    and grants silently vanish in production.

    So: seed a real row, run the real `launch()`, and drive the real runner
    with the env it produced. The only substitution is the HTTP transport —
    the pod's URL and bearer still come out of the launcher's env and are
    still checked by the real auth chain.
    """
    runner = _load_runner()

    # A row with grants of both kinds, entrypoints, and a sensitive harness
    # tool it must NOT end up being allowed.
    async with sf() as s:
        s.add(AgentDef(name="newsy", prompt="You are newsy.\n", model="sonnet",
                       description="Gathers the day's news.",
                       harness_tools=["WebSearch", "WebFetch", "Bash"],
                       platform_tools=[MEMORY], skills=["git"],
                       entrypoints={"crons": [{"schedule": "0 9 * * *",
                                               "prompt": "Morning brief."}],
                                    "webhooks": [], "topics": [],
                                    "timezone": "America/Toronto"}))
        run = Run(agent="newsy", trigger="schedule", requested_by="scheduler",
                  prompt="Morning brief.", state=RunState.RUNNING)
        s.add(run)
        await s.commit()
        run_id = run.id

    store = AgentStore(sf)
    await store.reload()
    app = app_over(sf, producer, secret_store, store, tmp_path)

    class _FakeBatch:
        job = None

        def create_namespaced_job(self, ns, job):
            self.job = job

    batch = _FakeBatch()
    launcher = K8sJobLauncher(
        batch=batch, settings=Settings(runner_image="r:1", k8s_namespace="ap",
                                       api_internal_url="http://t"),
        session_factory=sf, agent_store=store)
    async with sf() as s:
        await launcher.launch(await s.get(Run, run_id), store.get("newsy").manifest)

    env = {e.name: e.value for e in batch.job.spec.template.spec.containers[0].env}
    # The launcher's half of the contract: the pod is told who it is, where the
    # API is, and carries a token that unlocks its own definition and nothing
    # else. `AP_USER_MESSAGE` is absent — this is a plain run, not a turn.
    assert env["AP_RUN_ID"] == run_id and env["AP_AGENT"] == "newsy"
    assert env["AP_API_URL"] == "http://t" and env["AP_SESSION_TOKEN"]
    assert "AP_USER_MESSAGE" not in env

    monkeypatch.setenv("HOME", str(tmp_path / "pod-home"))
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    loop = asyncio.get_running_loop()

    async def _asgi(method: str, path: str, body: dict | None):
        async with caller(app) as c:
            r = await c.request(
                method, path, json=body,
                headers={"Authorization": "Bearer " + env["AP_SESSION_TOKEN"]})
            r.raise_for_status()
            return r.json()

    def _api_req(method, path, body=None):
        # Same signature and the same env the real one reads; only the wire is
        # swapped for the in-process app, on the test's loop.
        assert path.startswith("/api/runs/" + env["AP_RUN_ID"])
        return asyncio.run_coroutine_threadsafe(
            _asgi(method, path, body), loop).result(10)

    monkeypatch.setattr(runner, "_api_req", _api_req)

    def _pod_side():
        runner._install_agent("newsy")
        return (runner._agent_path("newsy").read_text(),
                runner._permission_args(self_edit=False, has_api_token=True,
                                        agent="newsy"))

    installed, args = await asyncio.to_thread(_pod_side)

    # The rendered file is the row: its identity (the CLI's required `name` and
    # `description` — without the latter it skips the file and the run dies on
    # "agent not found"), its grants in the row's order, and nothing the
    # payload carries that is not one of those.
    assert installed == ("---\nname: newsy\n"
                         'description: "Gathers the day\'s news."\n'
                         f"tools: WebSearch, WebFetch, Bash, {MEMORY}\n"
                         "---\n\nYou are newsy.\n")
    deny_at = args.index("--disallowedTools")
    allowed = args[args.index("--allowedTools") + 1:deny_at]
    # EXACTLY the row's grants — the launcher/API/runner chain neither drops
    # nor invents one — MINUS the sensitive set…
    assert allowed == ["WebSearch", "WebFetch", MEMORY]
    # …because `Bash` being declared does not make it usable: the runner's
    # unconditional deny is the layer a definition cannot argue with, and it
    # holds for a DB-delivered grant exactly as it did for a file-declared one.
    assert args[deny_at + 1:] == runner._SENSITIVE_TOOLS
    assert "--dangerously-skip-permissions" not in args

    # And the same token cannot read a different run's definition.
    other = Run(agent="newsy", trigger="manual", requested_by="t", prompt="x")
    async with sf() as s:
        s.add(other)
        await s.commit()
        other_id = other.id
    with pytest.raises(httpx.HTTPStatusError) as e:
        await _asgi("GET", f"/api/runs/{other_id}/agentdef", None)
    assert e.value.response.status_code == 403


# --- seam 4: the store's background refresh does not outlive the process -----

async def test_the_stores_refresh_task_is_cancelled_at_shutdown(sf, producer,
                                                                secret_store,
                                                                seed_agent, tmp_path):
    """A TTL refresh is scheduled, not awaited, so at shutdown one can be
    mid-query while the lifespan disposes the engine underneath it. The symptom
    is a bare "Task was destroyed but it is pending" — a pool teardown racing a
    live connection, reported without naming anything you can act on. The
    lifespan therefore cancels it explicitly."""
    await seed_agent("hello-world")
    store = AgentStore(sf, ttl_seconds=0)          # never auto-refreshes…
    await store.reload()
    app = app_over(sf, producer, secret_store, store, tmp_path)

    parked = asyncio.Event()
    released = asyncio.Event()

    async def _slow_reload():
        parked.set()
        await released.wait()                      # …so the test drives one

    async with app.router.lifespan_context(app):
        store.reload = _slow_reload
        store._refresh = asyncio.create_task(store._refresh_quietly())
        await parked.wait()
    assert store._refresh is None
    # Cancelled and AWAITED — not merely signalled and left pending.
    assert not released.is_set()


async def test_closing_a_store_that_never_refreshed_is_a_no_op(sf):
    store = AgentStore(sf)
    await store.aclose()
    await store.reload()
    await store.aclose()
    assert store._refresh is None


# --- the launcher/API/runner contract is one field list, spelled out twice ---

def test_the_agentdef_payload_and_the_renderer_agree_on_field_names():
    """`RunAgentDef` is the pod's whole view of its own definition, and the two
    sides of that contract live in different services with no shared type.
    Renaming a field on one side is therefore a runtime failure in a pod, and a
    quiet one: `_agentdef` swallows the exception and the mount fallback stands
    in, so the run keeps working on the LAST SYNCED definition instead of the
    row. Same pattern as the broker's field-split drift guard (T4).

    Each key is checked by removing it and requiring the rendering to change —
    a name the renderer does not actually read cannot pass."""
    from agentplatform.api.runs import RunAgentDef
    payload = {"name": "x", "prompt": "p", "description": "d",
               "harness_tools": ["WebFetch"],
               "platform_tools": [MEMORY], "skills": ["git"], "model": "sonnet"}
    assert set(RunAgentDef.model_fields) == set(payload)

    runner = _load_runner()
    full = runner._render_agent_md(payload)

    def without(field):
        try:
            return runner._render_agent_md({k: v for k, v in payload.items()
                                            if k != field})
        except KeyError:
            return "<required>"          # read, and not optional
    for field in ("name", "prompt", "description", "harness_tools",
                  "platform_tools"):
        assert without(field) != full, field
    # `skills` and `model` ride along for the "what is this pod running" view;
    # they are the launcher's to deliver (AP_SKILLS / the CLI flag), so the
    # renderer must NOT smuggle them into the definition file.
    assert "git" not in full and "sonnet" not in full
    assert full.count("tools:") == 1
