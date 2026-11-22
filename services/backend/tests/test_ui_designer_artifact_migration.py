"""Upgrade real old artifact columns without losing bytes or source identity."""
import os
import uuid

import pytest
from sqlalchemy import Column, MetaData, Table, inspect, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from agentplatform.db import Artifact, ArtifactBlob, UiDesignerDocument, _ensure_columns, utcnow
from agentplatform.ui_designer.artifact_migration import migrate_design_artifacts


async def upgrade_case(engine):
    metadata = MetaData()
    documents = UiDesignerDocument.__table__.to_metadata(metadata)
    old_artifacts = Table('artifacts', metadata, *[
        Column(column.name, column.type, primary_key=column.primary_key, nullable=column.nullable)
        for column in Artifact.__table__.columns
        if column.name not in ('ui_document_id', 'ui_provenance')])
    blobs = ArtifactBlob.__table__.to_metadata(metadata)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        now = utcnow()
        await conn.execute(documents.insert().values(id='a' * 32, slug='old-design',
            owner='user:owner', doc={'schema': 1, 'pages': []}, created_at=now))
        for identifier, meta in [('1' * 32, {'ui_designer': {'slug': 'old-design'}}),
                                 ('2' * 32, {}),
                                 ('3' * 32, {'parent_id': '1' * 32})]:
            await conn.execute(old_artifacts.insert().values(id=identifier, name='old.png',
                mime='image/png', size=5, sha256='f' * 64, kind='image',
                owner='user:owner', source='tool', meta=meta, tags=[], created_at=now))
            await conn.execute(blobs.insert().values(artifact_id=identifier, data=b'bytes'))
        await conn.run_sync(_ensure_columns)
        await migrate_design_artifacts(conn)
        await migrate_design_artifacts(conn)
        rows = (await conn.execute(select(Artifact.id, Artifact.ui_document_id,
            Artifact.ui_provenance, ArtifactBlob.data).join(ArtifactBlob,
            ArtifactBlob.artifact_id == Artifact.id).order_by(Artifact.id))).all()
        assert rows == [('1' * 32, 'a' * 32, True, b'bytes'),
                        ('2' * 32, None, False, b'bytes'),
                        ('3' * 32, 'a' * 32, True, b'bytes')]
        indexes = await conn.run_sync(lambda sync: inspect(sync).get_indexes('artifacts'))
        assert any(index['name'] == 'ix_artifacts_ui_document_id' for index in indexes)
        if conn.dialect.name == 'postgresql':
            columns = await conn.run_sync(lambda sync: inspect(sync).get_columns('artifacts'))
            provenance = next(column for column in columns if column['name'] == 'ui_provenance')
            assert not provenance['nullable'] and provenance['default'] == 'false'
    # The migration's nested session committed into the outer transaction.
    async with engine.connect() as conn:
        assert (await conn.execute(select(Artifact.ui_provenance).where(
            Artifact.id == '1' * 32))).scalar_one() is True


async def test_existing_sqlite_artifacts_upgrade_transactionally():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    try:
        await upgrade_case(engine)
    finally:
        await engine.dispose()


@pytest.mark.skipif(not os.environ.get('AP_TEST_PG_URL'), reason='requires disposable PostgreSQL AP_TEST_PG_URL')
async def test_existing_postgres_artifacts_upgrade_transactionally():
    url = os.environ['AP_TEST_PG_URL']
    schema = 'ui_artifact_upgrade_' + uuid.uuid4().hex
    control = create_async_engine(url)
    async with control.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_async_engine(url, connect_args={'server_settings': {'search_path': schema}})
    try:
        await upgrade_case(engine)
    finally:
        await engine.dispose()
        async with control.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        await control.dispose()
