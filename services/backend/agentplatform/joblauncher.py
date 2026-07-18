import asyncio
import logging
from kubernetes import client as k8s
from kubernetes.client.rest import ApiException
from agentplatform.agents import Manifest
from agentplatform.db import ACTIVE_STATES, Run, RunState, utcnow
from agentplatform.dispatcher import Launcher
from agentplatform.events import TOPIC_RUN_EVENTS

log = logging.getLogger("joblauncher")


class K8sJobLauncher(Launcher):
    def __init__(self, batch, settings):
        self.batch = batch
        self.settings = settings

    def build_job(self, run: Run, manifest: Manifest) -> k8s.V1Job:
        name = f"run-{run.id[:12]}"
        env = [
            k8s.V1EnvVar(name="AP_RUN_ID", value=run.id),
            k8s.V1EnvVar(name="AP_AGENT", value=run.agent),
            k8s.V1EnvVar(name="AP_PROMPT", value=run.prompt),
            k8s.V1EnvVar(name="AP_KAFKA_BOOTSTRAP", value=self.settings.kafka_bootstrap),
        ]
        container = k8s.V1Container(
            name="runner",
            image=self.settings.runner_image,
            env=env,
            volume_mounts=[
                k8s.V1VolumeMount(name="claude-credentials", mount_path="/secrets/claude", read_only=True),
                k8s.V1VolumeMount(name="agents", mount_path="/agents", read_only=True),
            ],
            resources=k8s.V1ResourceRequirements(
                requests={"memory": "1Gi"}, limits={"memory": "3Gi"}
            ),
        )
        volumes = [
            k8s.V1Volume(
                name="claude-credentials",
                secret=k8s.V1SecretVolumeSource(secret_name="claude-credentials"),
            ),
            k8s.V1Volume(
                name="agents",
                persistent_volume_claim=k8s.V1PersistentVolumeClaimVolumeSource(
                    claim_name=self.settings.agents_volume_claim
                ),
            ),
        ]
        pod_spec = k8s.V1PodSpec(
            containers=[container],
            volumes=volumes,
            restart_policy="Never",
        )
        job_spec = k8s.V1JobSpec(
            template=k8s.V1PodTemplateSpec(spec=pod_spec),
            backoff_limit=0,
            active_deadline_seconds=manifest.timeout_seconds,
        )
        return k8s.V1Job(
            metadata=k8s.V1ObjectMeta(name=name, namespace=self.settings.k8s_namespace),
            spec=job_spec,
        )

    async def launch(self, run: Run, manifest: Manifest) -> None:
        job = self.build_job(run, manifest)
        await asyncio.to_thread(self.batch.create_namespaced_job, self.settings.k8s_namespace, job)

    async def cancel(self, run_id: str) -> None:
        name = f"run-{run_id[:12]}"
        try:
            await asyncio.to_thread(
                self.batch.delete_namespaced_job,
                name,
                self.settings.k8s_namespace,
                propagation_policy="Foreground",
            )
        except ApiException as e:
            if e.status != 404:
                raise


class JobWatcher:
    def __init__(self, batch, settings, session_factory, producer):
        self.batch = batch
        self.settings = settings
        self.sf = session_factory
        self.producer = producer

    async def _event(self, run_id: str, state: str, detail: str = "") -> None:
        await self.producer.publish(
            TOPIC_RUN_EVENTS, run_id, {"run_id": run_id, "type": "state", "state": state, "detail": detail}
        )

    async def _set_state(self, run_id: str, state: RunState, error: str | None = None) -> None:
        async with self.sf() as s:
            db_run = await s.get(Run, run_id)
            if db_run is None:
                return
            db_run.state = state
            if error:
                db_run.error = error
            if state not in ACTIVE_STATES:
                db_run.finished_at = utcnow()
            await s.commit()
        await self._event(run_id, state, error or "")

    async def poll_once(self) -> None:
        async with self.sf() as s:
            from sqlalchemy import select
            rows = (await s.execute(
                select(Run).where(Run.state.in_([RunState.DISPATCHED, RunState.RUNNING]))
            )).scalars().all()
            runs = [(r.id, r.state) for r in rows]

        name_ns = self.settings.k8s_namespace
        for run_id, state in runs:
            name = f"run-{run_id[:12]}"
            try:
                job = await asyncio.to_thread(self.batch.read_namespaced_job, name, name_ns)
            except ApiException as e:
                if e.status == 404:
                    await self._set_state(run_id, RunState.FAILED, "job disappeared")
                else:
                    log.exception("failed to read job %s", name)
                continue

            status = job.status
            if status.failed:
                reason = ""
                deadline_exceeded = False
                for cond in status.conditions or []:
                    if getattr(cond, "reason", None) == "DeadlineExceeded":
                        deadline_exceeded = True
                        reason = "DeadlineExceeded"
                if deadline_exceeded:
                    await self._set_state(run_id, RunState.TIMED_OUT, reason)
                else:
                    await self._set_state(run_id, RunState.FAILED, reason or "job failed")
            elif status.succeeded:
                # Belt and braces: runner normally reports its own terminal event.
                await self._set_state(run_id, RunState.SUCCEEDED)
            elif status.active and state == RunState.DISPATCHED:
                await self._set_state(run_id, RunState.RUNNING)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:
                log.exception("poll_once failed")
            await asyncio.sleep(10)
