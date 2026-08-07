import asyncio
import logging
from kubernetes import client as k8s
from kubernetes.client.rest import ApiException
from agentplatform.agents import Manifest
from agentplatform.apikeys import generate_token, hash_token, revoke_run_keys, token_prefix
from agentplatform.db import ACTIVE_STATES, ApiKey, Run, RunState, SecretAccess, utcnow
from agentplatform.dispatcher import Launcher
from agentplatform.events import TOPIC_RUN_EVENTS

log = logging.getLogger("joblauncher")


class K8sJobLauncher(Launcher):
    # Audience for projected ServiceAccount tokens (design/13): bound to the
    # platform, NOT the k8s apiserver — a leaked token authenticates nowhere
    # but our own broker/API, and the kubelet rotates it automatically.
    IDENTITY_AUDIENCE = "agent-platform"

    def __init__(self, batch, settings, github_app=None, session_factory=None, skill_store=None,
                 agent_store=None, core=None, secret_store=None):
        self.batch = batch
        self.core = core
        self.settings = settings
        # For the run-JWT signing key (design/13 C); generated on first use.
        self.secret_store = secret_store
        self._runjwt_private: str | None = None
        # When set, coder-role runs are launched as self-edits: the runner
        # clones the repo, lets the agent edit it, and opens a PR using a
        # freshly minted App installation token.
        self.github_app = github_app
        self.sf = session_factory
        # Resolves an agent's skills → the secrets it may be bound. When set, a
        # pod gets exactly the union of its manifest + skill secrets (and the
        # base claude credential), nothing else.
        self.skill_store = skill_store
        # Lets launch() read the agent.md `tools:` line: declaring ANY
        # mcp__platform__* tool makes the run token-bearing (docs/design/12) —
        # a tools-scoped token when nothing broader applies.
        self.agent_store = agent_store
        # One operator API key per system agent, cached for the process
        # lifetime (plaintext isn't recoverable from the stored hash).
        self._system_tokens: dict[str, str] = {}
        self._sa_ready: set[str] = set()

    async def _system_token(self, agent: str) -> str:
        if agent not in self._system_tokens:
            token = generate_token()
            async with self.sf() as s:
                # Single-owner: this process's token REPLACES any predecessor.
                # The cache is in-memory, so every dispatcher restart re-mints;
                # without this, each restart left another live key behind.
                from sqlalchemy import select
                from agentplatform.db import utcnow
                stale = (await s.execute(select(ApiKey).where(
                    ApiKey.name == f"system:{agent}", ApiKey.run_id.is_(None),
                    ApiKey.revoked_at.is_(None)))).scalars().all()
                for k in stale:
                    k.revoked_at = utcnow()
                # `annotator`: narrow scope (read runs + annotate) so a
                # prompt-injected system agent can't trigger/kill runs or touch
                # anything else.
                s.add(ApiKey(name=f"system:{agent}", role="annotator", agent=agent,
                             key_hash=hash_token(token), prefix=token_prefix(token)))
                await s.commit()
            self._system_tokens[agent] = token
        return self._system_tokens[agent]

    async def _invoke_token(self, run: Run, role: str = "operator") -> str:
        """Mint a per-run token (role `operator` for invoke, `annotator` for
        memory-only), scoped to run.agent so its namespace/chain-depth are
        derived authoritatively. Tied to run.id and revoked when the run
        terminates (revoke_run_keys)."""
        token = generate_token()
        label = {"operator": "invoke", "annotator": "memory"}.get(role, role)
        async with self.sf() as s:
            s.add(ApiKey(name=f"{label}:{run.agent}", role=role, agent=run.agent,
                         run_id=run.id, key_hash=hash_token(token), prefix=token_prefix(token)))
            await s.commit()
        return token

    def _is_self_edit(self, manifest: Manifest) -> bool:
        return (manifest.role == "coder" and self.github_app is not None
                and bool(self.settings.git_remote_url) and bool(self.settings.github_repo))

    def _platform_token_role(self, agent: str) -> str | None:
        """The per-run token role an agent's `tools:` declaration earns, or
        None for no token. Core broker tools forward the token to the platform
        API, so they need a data role (annotator); custom tools only need the
        whoami-only `tools` role. Absent tools line = "all tools", but tokens
        follow EXPLICIT declaration only — auto-minting for every unrestricted
        agent would silently make ALL of them token-bearing."""
        if self.agent_store is None:
            return None
        from agentplatform.agentspec import PLATFORM_MCP_TOOLS, parse_agent_tools
        self.agent_store.reload()
        info = self.agent_store.get(agent)
        if info is None:
            return None
        declared = parse_agent_tools(info.agent_md)
        if declared is None:
            return None
        platform = [t for t in declared if t.startswith("mcp__platform__")]
        if not platform:
            return None
        return "annotator" if any(t in PLATFORM_MCP_TOOLS for t in platform) else "tools"

    def _ensure_service_account(self, agent: str) -> str:
        """Idempotently create the agent's ServiceAccount (`agent-<name>`) so
        the run pod can mount a projected identity token. Lazy at launch time —
        no provisioner ordering race for brand-new agents. The SA has ZERO
        RBAC: it exists purely as an attested identity."""
        name = f"agent-{agent}"
        if name in self._sa_ready:
            return name
        from kubernetes import client as k8s_client
        try:
            self.core.read_namespaced_service_account(name, self.settings.k8s_namespace)
        except ApiException as e:
            if e.status != 404:
                raise
            try:
                self.core.create_namespaced_service_account(
                    self.settings.k8s_namespace,
                    k8s_client.V1ServiceAccount(
                        metadata=k8s_client.V1ObjectMeta(
                            name=name,
                            labels={"app.kubernetes.io/name": "agent-platform",
                                    "agent-platform.io/agent": agent})))
            except ApiException as e2:
                if e2.status != 409:   # lost a create race — fine
                    raise
        self._sa_ready.add(name)
        return name

    async def _runjwt_key(self) -> str | None:
        """The ES256 private key for run JWTs, generated into the
        `run-jwt-key` secret on first use (design/13 C)."""
        if self._runjwt_private is not None:
            return self._runjwt_private
        if self.secret_store is None:
            return None
        from agentplatform import runjwt
        creds = await self.secret_store.get(runjwt.SECRET_NAME)
        if not (creds and creds.get("private_key")):
            creds = runjwt.generate_keypair()
            await self.secret_store.set(runjwt.SECRET_NAME, creds)
            log.info("generated run-jwt signing keypair")
        self._runjwt_private = creds["private_key"]
        return self._runjwt_private

    def _frozen_tools(self, agent: str) -> list[str]:
        """The mcp__platform__* grant set to freeze into a run JWT."""
        from agentplatform.agentspec import parse_agent_tools
        info = self.agent_store.get(agent) if self.agent_store else None
        declared = parse_agent_tools(info.agent_md) if info else None
        return [t for t in (declared or []) if t.startswith("mcp__platform__")]

    def bound_secrets(self, manifest: Manifest) -> list[str]:
        """The de-duplicated union of secret names an agent's pod may receive:
        its manifest `secrets` plus the secrets required by its skills. This is
        the whole allow-list — the pod is bound to these and nothing else."""
        names = list(manifest.secrets)
        if self.skill_store is not None:
            for s in self.skill_store.secrets_for(manifest.skills):
                if s not in names:
                    names.append(s)
        return names

    def build_job(self, run: Run, manifest: Manifest, self_edit_token: str | None = None,
                  api_token: str | None = None, sa_identity: str | None = None,
                  run_token: str | None = None, pod_sa: str | None = None) -> k8s.V1Job:
        name = f"run-{run.id[:12]}"
        env = [
            k8s.V1EnvVar(name="AP_RUN_ID", value=run.id),
            k8s.V1EnvVar(name="AP_AGENT", value=run.agent),
            k8s.V1EnvVar(name="AP_PROMPT", value=run.prompt),
            k8s.V1EnvVar(name="AP_KAFKA_BOOTSTRAP", value=self.settings.kafka_bootstrap),
        ]
        if manifest.model:
            env.append(k8s.V1EnvVar(name="AP_MODEL", value=manifest.model))
        # Token brokering (docs/design/09): with a claude-proxy configured the
        # runner is pointed at it instead of being handed the real token, and
        # the claude-credentials secret is not mounted at all (see volumes).
        if self.settings.claude_proxy_url:
            env.append(k8s.V1EnvVar(name="AP_CLAUDE_PROXY_URL", value=self.settings.claude_proxy_url))
        if manifest.skills:
            # The runner copies each named skill from the synced /agents/skills
            # tree into ~/.claude/skills so `claude` can use it.
            env.append(k8s.V1EnvVar(name="AP_SKILLS", value=",".join(manifest.skills)))
        talks_mcp = bool(api_token or sa_identity)
        # design/13 B: with SPIRE on, the pod's MCP traffic goes through a
        # local ghostunnel client that wraps it in SVID mTLS; claude itself
        # keeps speaking plain HTTP to localhost.
        mcp_url = ("http://127.0.0.1:8300/mcp" if self.settings.spire_enabled
                   else self.settings.mcp_broker_url)
        if api_token:
            env += [
                k8s.V1EnvVar(name="AP_API_URL", value=self.settings.api_internal_url),
                k8s.V1EnvVar(name="AP_API_TOKEN", value=api_token),
                # The MCP broker URL: the runner points claude at it (with the
                # token above as the auth header) so the agent gets brokered API
                # tools instead of a shell.
                k8s.V1EnvVar(name="AP_MCP_URL", value=mcp_url),
            ]
        elif sa_identity:
            # Workload identity (design/13): no secret in the env — the runner
            # reads the kubelet-rotated projected token from the file.
            env += [
                k8s.V1EnvVar(name="AP_API_URL", value=self.settings.api_internal_url),
                k8s.V1EnvVar(name="AP_API_TOKEN_FILE", value="/var/run/ap-identity/token"),
                k8s.V1EnvVar(name="AP_MCP_URL", value=mcp_url),
            ]
            if run_token:
                # Sender-constrained run JWT (design/13 C): proves WHICH run
                # with a frozen grant set, useless without this pod's SA.
                env.append(k8s.V1EnvVar(name="AP_RUN_TOKEN", value=run_token))
        if self_edit_token:
            env += [
                k8s.V1EnvVar(name="AP_SELF_EDIT", value="1"),
                k8s.V1EnvVar(name="AP_GIT_REMOTE_URL", value=self.settings.git_remote_url),
                k8s.V1EnvVar(name="AP_GITHUB_REPO", value=self.settings.github_repo),
                k8s.V1EnvVar(name="AP_DEFAULT_BRANCH", value=self.settings.default_branch),
                k8s.V1EnvVar(name="AP_GITHUB_TOKEN", value=self_edit_token),
            ]
        # Secret-binding: inject each bound secret's key/values as env vars via
        # envFrom. `optional` so a not-yet-configured secret doesn't wedge the
        # pod; the agent simply runs without it (the skill degrades). Unbound
        # secrets are never referenced, so the pod can't see them.
        env_from = [
            k8s.V1EnvFromSource(secret_ref=k8s.V1SecretEnvSource(name=s, optional=True))
            for s in self.bound_secrets(manifest)
        ]
        # Tighten the runner's cage: non-root, no privilege escalation, all
        # Linux capabilities dropped, and a READ-ONLY root filesystem — the only
        # writable paths are three explicit emptyDirs (the CLI writes $HOME/.claude,
        # clones self-edits to /workspace, and uses /tmp). A compromised agent
        # can't tamper with binaries or persist onto the root fs.
        volume_mounts = [
            k8s.V1VolumeMount(name="agents", mount_path="/agents", read_only=True),
            # Writable scratch (read-only rootfs otherwise). fsGroup makes
            # these group-writable by the runner (uid/gid 1001).
            k8s.V1VolumeMount(name="home", mount_path="/home/runner"),
            k8s.V1VolumeMount(name="workspace", mount_path="/workspace"),
            k8s.V1VolumeMount(name="tmp", mount_path="/tmp"),
        ]
        if not self.settings.claude_proxy_url:
            # Legacy direct-token mode only: the subscription token in the pod.
            volume_mounts.insert(0, k8s.V1VolumeMount(
                name="claude-credentials", mount_path="/secrets/claude", read_only=True))
        container = k8s.V1Container(
            name="runner",
            image=self.settings.runner_image,
            env=env,
            env_from=env_from or None,
            volume_mounts=volume_mounts,
            resources=k8s.V1ResourceRequirements(
                # CPU limit contains a runaway/malicious agent from starving the
                # single node; memory limit bounds its footprint.
                requests={"memory": "1Gi", "cpu": "250m"},
                limits={"memory": "3Gi", "cpu": "2"},
            ),
            security_context=k8s.V1SecurityContext(
                allow_privilege_escalation=False,
                run_as_non_root=True,
                # Numeric uid/gid of the image's `runner` user (1001 — node:22
                # already holds 1000). kubelet can't verify a non-numeric USER
                # name against runAsNonRoot, so it must be numeric here.
                run_as_user=1001,
                run_as_group=1001,
                read_only_root_filesystem=True,
                capabilities=k8s.V1Capabilities(drop=["ALL"]),
            ),
        )
        volumes = [
            k8s.V1Volume(
                name="agents",
                persistent_volume_claim=k8s.V1PersistentVolumeClaimVolumeSource(
                    claim_name=self.settings.agents_volume_claim
                ),
            ),
            k8s.V1Volume(name="home", empty_dir=k8s.V1EmptyDirVolumeSource()),
            k8s.V1Volume(name="workspace", empty_dir=k8s.V1EmptyDirVolumeSource()),
            k8s.V1Volume(name="tmp", empty_dir=k8s.V1EmptyDirVolumeSource()),
        ]
        if not self.settings.claude_proxy_url:
            volumes.insert(0, k8s.V1Volume(
                name="claude-credentials",
                secret=k8s.V1SecretVolumeSource(secret_name="claude-credentials"),
            ))
        if sa_identity:
            # Audience-bound projected token: valid ONLY against the platform
            # (not the k8s apiserver), TTL covers the longest run, kubelet
            # keeps it fresh. automount stays False — this explicit projection
            # is the pod's entire identity surface.
            volume_mounts.append(k8s.V1VolumeMount(
                name="ap-identity", mount_path="/var/run/ap-identity", read_only=True))
            volumes.append(k8s.V1Volume(
                name="ap-identity",
                projected=k8s.V1ProjectedVolumeSource(sources=[
                    k8s.V1VolumeProjection(
                        service_account_token=k8s.V1ServiceAccountTokenProjection(
                            audience=self.IDENTITY_AUDIENCE,
                            expiration_seconds=7200,
                            path="token"))])))
        init_containers = None
        if self.settings.spire_enabled and talks_mcp:
            # design/13 B: NATIVE sidecar (initContainer, restartPolicy Always
            # — terminates with the main container, so Jobs still complete).
            # It fetches the pod's SVID from the SPIRE agent socket and wraps
            # localhost MCP traffic in mTLS pinned to the broker's identity.
            # The runner container itself never sees a cert or the socket.
            td, ns = self.settings.spiffe_trust_domain, self.settings.k8s_namespace
            volumes.append(k8s.V1Volume(
                name="spiffe-workload-api",
                csi=k8s.V1CSIVolumeSource(driver="csi.spiffe.io", read_only=True)))
            init_containers = [k8s.V1Container(
                name="mcp-tunnel",
                image=self.settings.ghostunnel_image,
                restart_policy="Always",
                args=["client",
                      "--listen", "127.0.0.1:8300",
                      "--target", self.settings.mcp_broker_mtls_target,
                      "--use-workload-api-addr", self.settings.spiffe_workload_socket,
                      "--verify-uri",
                      f"spiffe://{td}/ns/{ns}/sa/{self.settings.broker_service_account}",
                      "--status", "http://0.0.0.0:8302"],
                # SPIRE registers entries per-pod, and a fresh run pod can beat
                # the entry propagation — the tunnel would be up but SVID-less
                # and claude would silently see zero MCP tools. Native-sidecar
                # semantics gate the runner on this probe, and client-mode
                # /_status only passes once a FULL TLS connection to the broker
                # succeeds (SVID in hand, server cert verified).
                startup_probe=k8s.V1Probe(
                    http_get=k8s.V1HTTPGetAction(path="/_status", port=8302),
                    period_seconds=2, failure_threshold=60),
                volume_mounts=[k8s.V1VolumeMount(
                    name="spiffe-workload-api",
                    mount_path="/spiffe-workload-api", read_only=True)],
                security_context=k8s.V1SecurityContext(
                    allow_privilege_escalation=False,
                    run_as_non_root=True, run_as_user=1001, run_as_group=1001,
                    read_only_root_filesystem=True,
                    capabilities=k8s.V1Capabilities(drop=["ALL"])),
                resources=k8s.V1ResourceRequirements(
                    requests={"memory": "32Mi", "cpu": "10m"},
                    limits={"memory": "64Mi"}),
            )]
        pod_spec = k8s.V1PodSpec(
            containers=[container],
            init_containers=init_containers,
            volumes=volumes,
            # Every run pod carries its agent's ServiceAccount so SPIRE can
            # attest it (identity = the SA); sa_identity separately controls
            # whether platform AUTH rides the projected token vs an API key.
            service_account_name=pod_sa or sa_identity,
            restart_policy="Never",
            # The runner never calls the k8s API — don't mount a ServiceAccount
            # token into the pod that runs agent code (removes that credential
            # and the API reach from the blast radius).
            automount_service_account_token=False,
            security_context=k8s.V1PodSecurityContext(
                seccomp_profile=k8s.V1SeccompProfile(type="RuntimeDefault"),
                # Group-own the emptyDir scratch volumes so the non-root runner
                # (gid 1001) can write them under the read-only root fs.
                fs_group=1001,
            ),
        )
        job_spec = k8s.V1JobSpec(
            template=k8s.V1PodTemplateSpec(
                # Label runner pods so NetworkPolicy can select them (e.g. allow
                # runner→kafka/api egress) and for observability.
                metadata=k8s.V1ObjectMeta(labels={
                    "app.kubernetes.io/name": "agent-platform",
                    "app.kubernetes.io/component": "runner",
                }),
                spec=pod_spec,
            ),
            backoff_limit=0,
            active_deadline_seconds=manifest.timeout_seconds,
            # GC finished run Jobs + their pods after this long. Safe: the run's
            # transcript/state/metrics are already persisted to postgres by the
            # recorder, and the UI reads history from there, not from pods.
            ttl_seconds_after_finished=self.settings.run_ttl_seconds,
        )
        return k8s.V1Job(
            metadata=k8s.V1ObjectMeta(name=name, namespace=self.settings.k8s_namespace),
            spec=job_spec,
        )

    async def launch(self, run: Run, manifest: Manifest) -> None:
        # The skills tree is git-synced under us; re-read so a skill's secret
        # bindings reflect the current definitions.
        if self.skill_store is not None:
            self.skill_store.reload()
        token = None
        if self._is_self_edit(manifest):
            token = await asyncio.to_thread(self.github_app.installation_token)
        api_token = None
        sa_identity = None
        if self.sf:
            if manifest.can_invoke:
                # Operator-scoped, per-run token: can invoke other agents (and,
                # as operator, save/recall its own memories).
                api_token = await self._invoke_token(run)
            elif manifest.system:
                # Narrow annotator token: read runs + annotate only.
                api_token = await self._system_token(run.agent)
            else:
                # docs/design/12: declaring platform tools IS the grant. Core
                # tools (they forward the token to our API) earn annotator;
                # custom-only earns the whoami-only `tools` role — a
                # credential-free agent declaring just `stocks` gains no
                # platform read/write surface. (The legacy `memory: true`
                # flag is retired; the memory tool declaration replaces it.)
                # docs/design/13 A: these agents carry IDENTITY, not a secret —
                # a projected SA token the API resolves back to the same role
                # ladder. Falls back to a minted key without a core client.
                role = self._platform_token_role(run.agent)
                if role is not None:
                    if self.core is not None:
                        sa_identity = await asyncio.to_thread(
                            self._ensure_service_account, run.agent)
                    else:
                        api_token = await self._invoke_token(run, role=role)
        pod_sa = None
        if self.core is not None:
            pod_sa = await asyncio.to_thread(self._ensure_service_account, run.agent)
        run_token = None
        if sa_identity:
            key = await self._runjwt_key()
            if key:
                from agentplatform import runjwt
                run_token = runjwt.mint(
                    key, run_id=run.id, agent=run.agent,
                    initiated_by=run.initiated_by or "admin",
                    tools=self._frozen_tools(run.agent), sa_name=sa_identity,
                    timeout_seconds=manifest.timeout_seconds
                        or self.settings.run_timeout_seconds)
        job = self.build_job(run, manifest, self_edit_token=token, api_token=api_token,
                             sa_identity=sa_identity, run_token=run_token, pod_sa=pod_sa)
        await self._audit_secret_access(run, manifest)
        await asyncio.to_thread(self.batch.create_namespaced_job, self.settings.k8s_namespace, job)

    async def _audit_secret_access(self, run: Run, manifest: Manifest) -> None:
        """Record the k8s secrets this run's pod is granted: the base claude
        credential (only in legacy direct-mount mode — with a claude-proxy the
        pod never sees it) plus the bound manifest/skill secrets. Best-effort —
        auditing must never block a launch."""
        if self.sf is None:
            return
        base = [] if self.settings.claude_proxy_url else ["claude-credentials"]
        granted = [*base, *self.bound_secrets(manifest)]
        try:
            async with self.sf() as s:
                for secret in granted:
                    s.add(SecretAccess(run_id=run.id, agent=run.agent, secret=secret))
                await s.commit()
        except Exception:
            log.exception("secret-access audit failed for run %s", run.id)

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
            TOPIC_RUN_EVENTS, run_id, {"run_id": run_id, "type": "state", "state": state, "detail": detail},
            type="run.state"
        )

    async def _set_state(self, run_id: str, state: RunState, error: str | None = None) -> None:
        async with self.sf() as s:
            db_run = await s.get(Run, run_id)
            if db_run is None:
                return
            if db_run.state not in ACTIVE_STATES:
                # Terminal states must never be regressed/clobbered by the watcher.
                # Re-checked against the current DB row, not the state snapshot the
                # poll loop started with, since the run may have transitioned
                # (e.g. cancelled) between listing and this write.
                return
            db_run.state = state
            if error:
                db_run.error = error
            if state not in ACTIVE_STATES:
                db_run.finished_at = utcnow()
                await revoke_run_keys(s, run_id)
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
