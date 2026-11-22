"""Install authoritative artifact provenance before serving document sharing."""
from sqlalchemy.ext.asyncio import AsyncSession


def _ensure_provenance_ddl(conn):
    from agentplatform.db import Artifact

    for index in Artifact.__table__.indexes:
        if index.name == 'ix_artifacts_ui_document_id':
            index.create(conn, checkfirst=True)
    if conn.dialect.name == 'postgresql':
        conn.exec_driver_sql('ALTER TABLE artifacts ALTER COLUMN ui_provenance SET DEFAULT false')
        conn.exec_driver_sql('ALTER TABLE artifacts ALTER COLUMN ui_provenance SET NOT NULL')


async def migrate_design_artifacts(conn):
    """Use init_db's outer transaction and cross-service advisory lock."""
    from .artifact_access import backfill_design_artifacts

    async with AsyncSession(bind=conn, expire_on_commit=False,
                            join_transaction_mode='create_savepoint') as session:
        await backfill_design_artifacts(session)
        await session.commit()
    await conn.run_sync(_ensure_provenance_ddl)
