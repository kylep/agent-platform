"""Actual READ COMMITTED race; opt in with a disposable AP_TEST_PG_URL."""

import asyncio
import os
import uuid
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from agentplatform.db import (
    Base,
    UiDesignerAsset,
    UiDesignerDocument,
    UiDesignerComment,
    UiDesignerDocumentAsset,
    UiDesignerFont,
    UiDesignerDocumentFont,
    UiDesignerImport,
    UiDesignerVersion,
)
from agentplatform.ui_designer import store


@pytest.mark.skipif(
    not os.environ.get("AP_TEST_PG_URL"),
    reason="requires disposable PostgreSQL AP_TEST_PG_URL",
)
@pytest.mark.parametrize('kind', ['asset', 'font'])
async def test_gc_rechecks_placement_after_waiting_for_copy_asset_lock(kind):
    url = os.environ["AP_TEST_PG_URL"]
    schema = "library_gc_" + uuid.uuid4().hex
    control = create_async_engine(url)
    async with control.begin() as connection:
        await connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": schema}}
    )
    reached_lock = asyncio.Event()
    gc_backend = {}

    class GcSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            if (
                getattr(statement, "is_delete", False)
                or getattr(statement, "_for_update_arg", None) is not None
            ):
                reached_lock.set()
            return await super().execute(statement, *args, **kwargs)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    gc_factory = async_sessionmaker(engine, class_=GcSession, expire_on_commit=False)
    blob_model = UiDesignerAsset if kind == 'asset' else UiDesignerFont
    placement_model = UiDesignerDocumentAsset if kind == 'asset' else UiDesignerDocumentFont
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync: Base.metadata.create_all(
                    sync,
                    tables=[
                        UiDesignerDocument.__table__,
                        UiDesignerComment.__table__,
                        UiDesignerVersion.__table__,
                        UiDesignerImport.__table__,
                        UiDesignerAsset.__table__,
                        UiDesignerDocumentAsset.__table__,
                        UiDesignerFont.__table__,
                        UiDesignerDocumentFont.__table__,
                    ],
                )
            )
        async with factory() as session:
            if kind == 'asset':
                sha = await store.add_asset(session, data=b"library-copy-race", mime="image/png")
            else:
                sha = 'a' * 64
                session.add(UiDesignerFont(sha256=sha, data=b'font-copy-race', bytes=14,
                    family='Race Font', weight=400, font_meta={'license': 'OFL-1.1'}))
            await session.commit()
        async with factory() as copying:
            await copying.execute(
                select(blob_model.sha256)
                .where(blob_model.sha256 == sha)
                .with_for_update()
            )

            async def collect():
                async with gc_factory() as session:
                    gc_backend["pid"] = (
                        await session.execute(text("SELECT pg_backend_pid()"))
                    ).scalar_one()
                    result = await store.gc_assets(session)
                    await session.commit()
                    return result

            task = asyncio.create_task(collect())
            try:
                await asyncio.wait_for(reached_lock.wait(), timeout=5)
                # Observe the database wait, not merely Python scheduling before
                # execute(): the old DELETE must already hold its stale snapshot.
                async with control.connect() as observer:
                    for _ in range(500):
                        await observer.execute(text("SELECT pg_stat_clear_snapshot()"))
                        waiting = (
                            await observer.execute(
                                text(
                                    "SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"
                                ),
                                {"pid": gc_backend["pid"]},
                            )
                        ).scalar_one_or_none()
                        if waiting == "Lock":
                            break
                        await asyncio.sleep(0.01)
                    else:
                        pytest.fail(
                            "GC did not reach its PostgreSQL asset-row lock wait"
                        )
                assert not task.done()
                copying.add(
                    placement_model(
                        document_id=uuid.uuid4().hex,
                        sha256=sha,
                        placed_by="user:test",
                        **({'placed_from': 'library:source:1'} if kind == 'asset' else {}),
                    )
                )
                await copying.commit()
                result = await asyncio.wait_for(task, timeout=5)
                assert result.get('assets_removed' if kind == 'asset' else 'fonts_removed', 0) == 0
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        async with factory() as session:
            assert await session.get(blob_model, sha) is not None
    finally:
        await engine.dispose()
        async with control.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await control.dispose()
