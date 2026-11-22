"""Relay and comment wakes serialize one shared spending reservation on Postgres."""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentplatform.config import Settings
from agentplatform.db import Base, RelayInvocation, UiDesignerCommentWake, utcnow
from agentplatform.relay_router import RelayRouter
from agentplatform.ui_designer.comment_wakes import invocation_budget_lock

pytestmark = pytest.mark.skipif(not os.environ.get("AP_TEST_PG_URL"),
                               reason="requires disposable PostgreSQL AP_TEST_PG_URL")


async def test_relay_waits_for_comment_budget_reservation():
    url = os.environ["AP_TEST_PG_URL"]
    schema = "comment_budget_" + uuid.uuid4().hex
    control = create_async_engine(url)
    async with control.begin() as connection:
        await connection.execute(text(f"CREATE SCHEMA {schema}"))
    engines = [create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
               for _ in range(2)]
    waiting = asyncio.Event()
    class ObservedSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            if "pg_advisory_xact_lock" in str(statement):
                waiting.set()
            return await super().execute(statement, *args, **kwargs)
    comment_sessions = async_sessionmaker(engines[0], expire_on_commit=False)
    relay_sessions = async_sessionmaker(engines[1], class_=ObservedSession, expire_on_commit=False)
    router = RelayRouter(Settings(relay_global_invocations_per_hour=1), relay_sessions, None, None)
    task = None
    try:
        async with engines[0].begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync,
                tables=[RelayInvocation.__table__, UiDesignerCommentWake.__table__]))
        async with comment_sessions() as comment:
            await invocation_budget_lock(comment)
            now = utcnow()
            comment.add(UiDesignerCommentWake(document_id="d" * 32, comment_id="c" * 32,
                target="artist", version=1, author="user:owner", initiating_principal="user:owner",
                state="claimed", run_id="a" * 32, created_at=now, updated_at=now))
            await comment.flush()
            async def check_relay():
                async with relay_sessions() as session:
                    return await router._spend(session, "r" * 32)
            task = asyncio.create_task(check_relay())
            await asyncio.wait_for(waiting.wait(), 5)
            assert not task.done()
            await comment.commit()
        assert await asyncio.wait_for(task, 5) == (0, 1)
        assert router._exhausted(0, 1) == 1
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for engine in engines:
            await engine.dispose()
        async with control.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await control.dispose()
