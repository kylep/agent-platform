"""Declarative app provisioning (docs/design/11). A heartbeat in the
dispatcher reconciles every declared app (apps/<name>/app.yaml in the synced
checkout) with the resources its manifest claims:

- `needs.postgres` → a dedicated role + schema (app_<name>) in the platform's
  postgres, credentials delivered as k8s secret `app-<name>-db`. The app owns
  ONLY its schema — isolation by grant, not by trust.
- `agent_key` → a single-owner platform API key (`app:<name>`, scoped by the
  declared role), delivered as k8s secret `app-<name>-key`. Stale same-name
  keys are revoked on mint — the joblauncher's system-token discipline.
- `needs.kafka_topics` → topics created if missing (namespace-validated
  app.<name>.* by the registry).

Everything is idempotent: the loop converges, it never tears down (removing
an app.yaml leaves its data — deletion is a human act)."""
from __future__ import annotations

import asyncio
import logging
import re
import secrets as pysecrets

from sqlalchemy import select, text

from agentplatform.apikeys import generate_token, hash_token, token_prefix
from agentplatform.db import ApiKey, utcnow

log = logging.getLogger("app-provisioner")

_NAME = re.compile(r"^[a-z][a-z0-9-]*$")


def pg_ident(app_name: str) -> str:
    """apps are kebab-case; pg identifiers are snake_case."""
    if not _NAME.match(app_name):
        raise ValueError(f"invalid app name `{app_name}`")
    return "app_" + app_name.replace("-", "_")


class AppProvisioner:
    def __init__(self, registry, engine, session_factory, secret_store, settings,
                 interval_seconds: int = 300):
        self.registry = registry
        self.engine = engine
        self.sf = session_factory
        self.secrets = secret_store
        self.settings = settings
        self.interval = interval_seconds

    async def provision_once(self) -> dict[str, list[str]]:
        """Reconcile every declared app; returns {app: [actions taken]}."""
        self.registry.reload()
        out: dict[str, list[str]] = {}
        for info in self.registry.list():
            if info.spec is None:
                log.warning("app %s has a broken app.yaml: %s", info.name, info.error)
                continue
            actions: list[str] = []
            try:
                if info.spec.needs.postgres:
                    actions += await self._ensure_postgres(info.spec.name)
                if info.spec.agent_key is not None:
                    actions += await self._ensure_key(info.spec.name, info.spec.agent_key.role)
                if info.spec.needs.kafka_topics:
                    actions += await self._ensure_topics(info.spec.needs.kafka_topics)
            except Exception:
                log.exception("provisioning %s failed", info.name)
            if actions:
                log.info("provisioned %s: %s", info.name, ", ".join(actions))
            out[info.name] = actions
        return out

    # --- postgres -------------------------------------------------------------

    async def _ensure_postgres(self, app: str) -> list[str]:
        ident = pg_ident(app)
        secret_name = f"app-{app}-db"
        existing = await self.secrets.get(secret_name)
        password = (existing or {}).get("password") or pysecrets.token_urlsafe(24)
        actions = []
        async with self.engine.begin() as conn:
            role = (await conn.execute(text(
                "SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": ident})).scalar()
            if not role:
                # identifiers can't be bound parameters; ident is regex-safe.
                await conn.execute(text(
                    f'CREATE ROLE "{ident}" LOGIN PASSWORD :pw'), {"pw": password})
                actions.append(f"role {ident}")
            schema = (await conn.execute(text(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
                {"s": ident})).scalar()
            if not schema:
                await conn.execute(text(f'CREATE SCHEMA "{ident}" AUTHORIZATION "{ident}"'))
                actions.append(f"schema {ident}")
        if existing is None:
            # The app connects to the same server/database the platform uses,
            # as its own role, confined to its own schema.
            from sqlalchemy.engine.url import make_url
            u = make_url(self.settings.db_url)
            host, port, db = u.host or "localhost", u.port or 5432, u.database or "postgres"
            await self.secrets.set(secret_name, {
                "username": ident, "password": password,
                "database": db, "host": host, "port": str(port),
                "url": f"postgresql+asyncpg://{ident}:{password}@{host}:{port}/{db}",
            })
            actions.append(f"secret {secret_name}")
        return actions

    # --- platform api key -----------------------------------------------------

    async def _ensure_key(self, app: str, role: str) -> list[str]:
        secret_name = f"app-{app}-key"
        key_name = f"app:{app}"
        existing = await self.secrets.get(secret_name)
        async with self.sf() as s:
            active = (await s.execute(select(ApiKey).where(
                ApiKey.name == key_name, ApiKey.revoked_at.is_(None)))).scalars().all()
            if existing is not None and any(k.role == role for k in active):
                return []
            # Single-owner: revoke every predecessor, mint fresh, deliver.
            for k in active:
                k.revoked_at = utcnow()
            token = generate_token()
            s.add(ApiKey(name=key_name, role=role, agent=None,
                         key_hash=hash_token(token), prefix=token_prefix(token)))
            await s.commit()
        await self.secrets.set(secret_name, {"AP_API_TOKEN": token})
        return [f"key {key_name} ({role})"]

    # --- kafka ----------------------------------------------------------------

    async def _ensure_topics(self, topics: list[str]) -> list[str]:
        from aiokafka.admin import AIOKafkaAdminClient, NewTopic
        from aiokafka.errors import TopicAlreadyExistsError
        admin = AIOKafkaAdminClient(bootstrap_servers=self.settings.kafka_bootstrap)
        actions = []
        try:
            await admin.start()
            known = set(await admin.list_topics())
            missing = [t for t in topics if t not in known]
            for t in missing:
                try:
                    await admin.create_topics([NewTopic(name=t, num_partitions=6,
                                                        replication_factor=1)])
                    actions.append(f"topic {t}")
                except TopicAlreadyExistsError:
                    pass
        finally:
            await admin.close()
        return actions

    async def run_forever(self) -> None:
        while True:
            try:
                await self.provision_once()
            except Exception:
                log.exception("provision loop failed")
            await asyncio.sleep(self.interval)
