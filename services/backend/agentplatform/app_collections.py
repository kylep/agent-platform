"""DB-owned App collections, with a one-way import of legacy app identities."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from agentplatform.db import AppCollection

APP_NAME = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


async def import_legacy_apps(session_factory, registry) -> None:
    """Import missing app identities without overwriting DB edits.

    Startup may run in several API replicas. A savepoint makes a competing
    insert harmless while leaving the outer transaction usable.
    """
    registry.reload()
    async with session_factory() as session:
        existing = set((await session.execute(select(AppCollection.name))).scalars())
        for info in registry.list():
            if info.name in existing or info.spec is None:
                continue
            spec = info.spec
            try:
                async with session.begin_nested():
                    session.add(AppCollection(
                        name=info.name,
                        display_name=spec.display_name or info.name,
                        description=spec.description,
                        icon=spec.icon,
                        source_app=info.name,
                    ))
                    await session.flush()
            except IntegrityError:
                pass
        await session.commit()
