import asyncio, json, logging
from sqlalchemy import func, select
from agentplatform.agents import AgentStore, Manifest
from agentplatform.apikeys import revoke_run_keys
from agentplatform.db import ACTIVE_STATES, Run, RunState, utcnow
from agentplatform.events import (TOPIC_RUN_DLQ, TOPIC_RUN_EVENTS, TOPIC_RUN_REQUESTS)

log = logging.getLogger("dispatcher")

class Launcher:
    async def launch(self, run: Run, manifest: Manifest) -> None: raise NotImplementedError
    async def cancel(self, run_id: str) -> None: raise NotImplementedError

class FakeLauncher(Launcher):
    def __init__(self):
        self.launched: list[str] = []; self.cancelled: list[str] = []
        self.fail_next = False
    async def launch(self, run, manifest):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("boom")
        self.launched.append(run.id)
    async def cancel(self, run_id): self.cancelled.append(run_id)

class Dispatcher:
    def __init__(self, settings, session_factory, producer, agent_store: AgentStore, launcher: Launcher):
        self.settings, self.sf, self.producer = settings, session_factory, producer
        self.agents, self.launcher = agent_store, launcher

    async def _event(self, run_id: str, state: str, detail: str = "") -> None:
        await self.producer.publish(TOPIC_RUN_EVENTS, run_id,
            {"run_id": run_id, "type": "state", "state": state, "detail": detail})

    async def _set_state(self, run: Run, state: RunState, error: str | None = None) -> None:
        async with self.sf() as s:
            db_run = await s.get(Run, run.id)
            db_run.state = state
            if error: db_run.error = error
            if state == RunState.DISPATCHED: db_run.started_at = utcnow()
            if state in (RunState.REJECTED, RunState.DLQ, RunState.KILLED):
                db_run.finished_at = utcnow()
                await revoke_run_keys(s, run.id)
            await s.commit()
        await self._event(run.id, state, error or "")

    async def handle(self, message: dict) -> None:
        run_id = message.get("run_id", "")
        async with self.sf() as s:
            run = await s.get(Run, run_id)
        if run is None: return
        if message.get("type") == "cancel":
            if run.state in ACTIVE_STATES:
                await self.launcher.cancel(run_id)
                await self._set_state(run, RunState.KILLED)
            return
        if run.state != RunState.QUEUED: return  # idempotency
        # The synced checkout changes underneath us (agents-sync pulls git);
        # re-scan so agents added after boot are dispatchable.
        self.agents.reload()
        info = self.agents.get(run.agent)
        if info is None or info.error is not None:
            await self._set_state(run, RunState.REJECTED, "unknown or quarantined agent")
            return
        manifest = info.manifest
        async with self.sf() as s:
            busy = (await s.execute(select(func.count()).select_from(Run)
                    .where(Run.state.in_([RunState.DISPATCHED, RunState.RUNNING])))).scalar_one()
            agent_busy = (await s.execute(select(func.count()).select_from(Run)
                    .where(Run.agent == run.agent,
                           Run.state.in_([RunState.DISPATCHED, RunState.RUNNING])))).scalar_one()
        if busy >= self.settings.global_concurrency or agent_busy >= manifest.concurrency:
            await asyncio.sleep(5)
            await self.producer.publish(TOPIC_RUN_REQUESTS, run_id, message)
            return
        try:
            await self.launcher.launch(run, manifest)
        except Exception as e:
            await self._set_state(run, RunState.DLQ, str(e))
            await self.producer.publish(TOPIC_RUN_DLQ, run_id,
                                        {"message": message, "error": str(e)})
            return
        await self._set_state(run, RunState.DISPATCHED)

    async def run_forever(self) -> None:
        from aiokafka import AIOKafkaConsumer
        consumer = AIOKafkaConsumer(TOPIC_RUN_REQUESTS,
                                    bootstrap_servers=self.settings.kafka_bootstrap,
                                    group_id="dispatcher", enable_auto_commit=False)
        await consumer.start(); await self.producer.start()
        try:
            async for msg in consumer:
                try:
                    await self.handle(json.loads(msg.value))
                except Exception:
                    log.exception("handle failed")
                await consumer.commit()
        finally:
            await consumer.stop(); await self.producer.stop()

    async def sweep_queued(self, older_than_seconds: int = 15) -> int:
        """Drain queued runs whose run-request message never made it to Kafka
        (e.g. the API accepted the run while Kafka was down). handle() is
        idempotent, so re-driving a run that also has a pending message is a
        no-op for whichever copy arrives second."""
        from datetime import timedelta
        cutoff = utcnow() - timedelta(seconds=older_than_seconds)
        async with self.sf() as s:
            rows = (await s.execute(
                select(Run.id).where(Run.state == RunState.QUEUED,
                                     Run.created_at < cutoff))).scalars().all()
        for run_id in rows:
            try:
                await self.handle({"type": "run", "run_id": run_id})
            except Exception:
                log.exception("sweep handle failed for %s", run_id)
        return len(rows)

    async def sweep_forever(self, interval_seconds: int = 15) -> None:
        while True:
            try:
                await self.sweep_queued()
            except Exception:
                log.exception("sweep_queued failed")
            await asyncio.sleep(interval_seconds)
