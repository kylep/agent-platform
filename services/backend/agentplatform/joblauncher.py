import asyncio
import logging

from kubernetes import client as k8s
from kubernetes.client.rest import ApiException

from agentplatform.agents import Manifest
from agentplatform.apikeys import (
    generate_token,
    hash_token,
    revoke_run_keys,
    token_prefix,
)
from agentplatform.db import ACTIVE_STATES, ApiKey, Run, RunState, SecretAccess, utcnow
from agentplatform.dispatcher import Launcher
from agentplatform.events import TOPIC_RUN_EVENTS

log = logging.getLogger("joblauncher")


class K8sJobLauncher(Launcher):
    # Audience for projected ServiceAccount tokens (design/13): bound to the
    # platform, NOT the k8s apiserver — a leaked token authenticates nowhere
    # but our own broker/API, and the kubelet rotates it automatically.
    IDENTITY_AUDIENCE = "agent-platform"

    def __init__(self, batch, settings, session_factory=None, skill_store=None,
                 agent_store=None, core=None, secret_store=None):
        self.batch = batch
        self.core = core
        self.settings = settings
        # For the run-JWT signing key (design/13 C); generated on first use.
        self.secret_store = secret_store
        self._runjwt_private: str | None = None
        self.sf = session_factory
        # Retained for launch compatibility; skills do not grant secrets.
        self.skill_store = skill_store
        # Lets launch() read the agent's tool grants: holding ANY
        # mcp__platform__* tool makes the run token-bearing (docs/design/12) —
        # a tools-scoped token when nothing broader applies.
        self.agent_store = agent_store
        self._sa_ready: set[str] = set()

    async def _invoke_token(self, run: Run, role: str = "operator",
                            label: str | None = None) -> str:
        """Mint a per-run token (role `operator` for invoke, `annotator` for
        memory-only), scoped to run.agent so its namespace/chain-depth are
        derived authoritatively. Tied to run.id and revoked when the run
        terminates (revoke_run_keys).

        The label is what the keys page and the audit trail call the key. Only
        the roles whose name is not self-explanatory are translated; the
        rest — `tools`, `relay` — already say what they are, so they fall
        through as `relay:<agent>` rather than needing an entry each."""
        token = generate_token()
        label = label or {"operator": "invoke", "annotator": "memory"}.get(role, role)
        async with self.sf() as s:
            s.add(ApiKey(name=f"{label}:{run.agent}", role=role, agent=run.agent,
                         run_id=run.id, key_hash=hash_token(token), prefix=token_prefix(token)))
            await s.commit()
        return token

    def _is_dev(self, manifest: Manifest) -> bool:
        """The Workbench profile (docs/design/24). Keyed on the role alone:
        the pod is handed no repository credential — its clone is anonymous
        and its one way out is the API's publish route, which holds the App."""
        return manifest.role == "dev"

    def _platform_token_role(self, manifest: Manifest) -> str | None:
        """The per-run token role an agent's PLATFORM-TOOL GRANTS earn, or None
        for no token. No platform grant, no token — a token follows an explicit
        grant, never an agent merely existing.

        The ladder itself lives in `agentspec.platform_token_role`, shared with
        the API's two identity paths so a run cannot launch on one rung and be
        authorized on another: core broker tools forward the token to the
        platform API and earn `annotator`, the relay grant earns the
        Relay-only `relay` (docs/design/19), anything else custom earns the
        whoami-only `tools`.

        The manifest is the run's frozen definition snapshot (design 27), so a
        grant edit cannot change a queued retry or a live run."""
        from agentplatform.agentspec import platform_token_role
        return platform_token_role(manifest.platform_tools)

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

    def _frozen_tools(self, manifest: Manifest) -> list[str]:
        """The mcp__platform__* grant set to freeze into a run JWT (design/13
        C), read from the run's frozen definition snapshot."""
        return [t for t in manifest.platform_tools if t.startswith("mcp__platform__")]

    def bound_secrets(self, manifest: Manifest) -> list[str]:
        """Only explicit agent secret bindings may enter the pod."""
        # Provider credentials have dedicated delivery paths and may never be
        # smuggled into an agent through an explicit manifest binding.
        reserved = {"claude-credentials", "codex-credentials"}
        names = [name for name in manifest.secrets if name not in reserved]
        return names

    def build_job(self, run: Run, manifest: Manifest, api_token: str | None = None,
                  sa_identity: str | None = None,
                  run_token: str | None = None, pod_sa: str | None = None,
                  session_token: str | None = None, dev: bool = False) -> k8s.V1Job:
        name = f"run-{run.id[:12]}"
        env = [
            k8s.V1EnvVar(name="AP_RUN_ID", value=run.id),
            k8s.V1EnvVar(name="AP_AGENT", value=run.agent),
            k8s.V1EnvVar(name="AP_RUNTIME", value=manifest.runtime),
            k8s.V1EnvVar(name="AP_PROMPT", value=run.prompt),
            k8s.V1EnvVar(name="AP_KAFKA_BOOTSTRAP", value=self.settings.kafka_bootstrap),
        ]
        if manifest.model:
            env.append(k8s.V1EnvVar(name="AP_MODEL", value=manifest.model))
        # Token brokering (docs/design/09): with a claude-proxy configured the
        # runner is pointed at it instead of being handed the real token, and
        # the claude-credentials secret is not mounted at all (see volumes).
        if manifest.runtime == "claude" and self.settings.claude_proxy_url:
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
        if dev:
            # The Workbench (docs/design/24): the remote URL is for an ANONYMOUS
            # clone, and no AP_GITHUB_TOKEN / AP_SELF_EDIT ever joins it — the
            # pod cannot push; the API publishes the branch from a bundle.
            env += [
                k8s.V1EnvVar(name="AP_WORKSPACE", value="dev"),
                k8s.V1EnvVar(name="AP_GIT_REMOTE_URL", value=self.settings.git_remote_url),
                k8s.V1EnvVar(name="AP_DEFAULT_BRANCH", value=self.settings.default_branch),
                k8s.V1EnvVar(name="AP_MAX_TURNS", value=str(self.settings.dev_max_turns)),
                k8s.V1EnvVar(name="AP_VERIFY_TIMEOUT",
                             value=str(self.settings.dev_verify_timeout_seconds)),
                k8s.V1EnvVar(name="AP_WEB_URL", value=self.settings.web_internal_url),
                # The runner refuses to bundle past this itself, so an
                # oversized change is a clear line in the thread rather than a
                # 413 from the publish route after the upload.
                k8s.V1EnvVar(name="AP_PUBLISH_MAX_BYTES",
                             value=str(self.settings.publish_max_bytes)),
                # Where the dev image bakes Playwright's browsers; without it
                # Playwright looks in $HOME, which is a fresh emptyDir.
                k8s.V1EnvVar(name="PLAYWRIGHT_BROWSERS_PATH", value="/ms-playwright"),
            ]
        # The run-scoped session token (docs/design/14, widened by design/15):
        # it unlocks this run's own endpoints and nothing else — the agent
        # DEFINITION the pod materializes, and, for a conversation turn, the
        # session blob it resumes with AP_USER_MESSAGE as the new turn.
        # AP_API_URL may already be set by a token-bearing agent above — dedupe
        # so the runner reads a single value.
        if session_token:
            env.append(k8s.V1EnvVar(name="AP_SESSION_TOKEN", value=session_token))
            if run.conversation_id:
                # Conversation-only: a non-empty AP_USER_MESSAGE is what makes
                # the runner try to resume, so a plain run must not carry one.
                env.append(k8s.V1EnvVar(name="AP_USER_MESSAGE",
                                        value=run.user_message or ""))
            if not any(e.name == "AP_API_URL" for e in env):
                env.append(k8s.V1EnvVar(name="AP_API_URL", value=self.settings.api_internal_url))
        if manifest.runtime == "codex" and self.settings.codex_proxy_url:
            # The broker owns OAuth. The runner receives only this service URL
            # and configures Codex with a harmless placeholder bearer.
            env.append(k8s.V1EnvVar(name="AP_CODEX_PROXY_URL",
                                    value=self.settings.codex_proxy_url))
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
        # uses /workspace for Workbench checkouts, and uses /tmp). A compromised agent
        # can't tamper with binaries or persist onto the root fs.
        volume_mounts = [
            k8s.V1VolumeMount(name="agents", mount_path="/agents", read_only=True),
            # Writable scratch (read-only rootfs otherwise). fsGroup makes
            # these group-writable by the runner (uid/gid 1001).
            k8s.V1VolumeMount(name="home", mount_path="/home/runner"),
            k8s.V1VolumeMount(name="workspace", mount_path="/workspace"),
            k8s.V1VolumeMount(name="tmp", mount_path="/tmp"),
        ]
        if dev:
            # Chromium's shared memory. Under a read-only rootfs the default
            # 64Mi /dev/shm is what the browser crashes on, so the dev profile
            # backs it with a bounded in-memory emptyDir — the same cage, one
            # more scratch path.
            volume_mounts.append(k8s.V1VolumeMount(name="dshm", mount_path="/dev/shm"))
        if manifest.runtime == "claude" and not self.settings.claude_proxy_url:
            # Legacy direct-token mode only: the subscription token in the pod.
            volume_mounts.insert(0, k8s.V1VolumeMount(
                name="claude-credentials", mount_path="/secrets/claude", read_only=True))
        if dev:
            # A clone, `npm ci`, a pytest suite and a browser at once: the lean
            # profile's ceiling would OOM the verify step, not the model.
            resources = k8s.V1ResourceRequirements(
                requests={"memory": "2Gi", "cpu": "500m"},
                limits={"memory": "6Gi", "cpu": "3"},
            )
        else:
            resources = k8s.V1ResourceRequirements(
                # CPU limit contains a runaway/malicious agent from starving the
                # single node; memory limit bounds its footprint.
                requests={"memory": "1Gi", "cpu": "250m"},
                limits={"memory": "3Gi", "cpu": "2"},
            )
        container = k8s.V1Container(
            name="runner",
            image=self.settings.runner_dev_image if dev else self.settings.runner_image,
            env=env,
            env_from=env_from or None,
            volume_mounts=volume_mounts,
            resources=resources,
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
            # The workspace is unbounded for the lean profile (a --depth 1
            # clone); a dev run's clone plus node_modules gets a size limit so
            # a runaway build evicts the pod rather than filling the node.
            k8s.V1Volume(name="workspace", empty_dir=k8s.V1EmptyDirVolumeSource(
                size_limit=self.settings.dev_workspace_size_limit if dev else None)),
            k8s.V1Volume(name="tmp", empty_dir=k8s.V1EmptyDirVolumeSource()),
        ]
        if dev:
            volumes.append(k8s.V1Volume(name="dshm", empty_dir=k8s.V1EmptyDirVolumeSource(
                medium="Memory", size_limit=self.settings.dev_shm_size_limit)))
        if manifest.runtime == "claude" and not self.settings.claude_proxy_url:
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
                    "agent-platform/runtime": manifest.runtime,
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
        api_token = None
        sa_identity = None
        if self.sf:
            if manifest.can_invoke:
                # Operator-scoped, per-run token: can invoke other agents (and,
                # as operator, save/recall its own memories).
                api_token = await self._invoke_token(run)
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
                role = self._platform_token_role(manifest)
                if role is not None:
                    if self.core is not None:
                        sa_identity = await asyncio.to_thread(
                            self._ensure_service_account, run.agent)
                    else:
                        api_token = await self._invoke_token(run, role=role)
        session_token = None
        if self.sf:
            # EVERY run gets the narrow `session`-role per-run token, regardless
            # of the agent's tool grants. It reaches exactly two run-scoped
            # endpoints: the agent definition the pod materializes into
            # ~/.claude/agents (docs/design/15 — definitions are rows, not files
            # on the mount) and, for conversation turns, the session blob
            # (docs/design/14). Widening it from conversations to all runs grants
            # no new surface: both endpoints refuse any run but this one.
            session_token = await self._invoke_token(run, role="session")
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
                    tools=self._frozen_tools(manifest), sa_name=sa_identity,
                    timeout_seconds=manifest.timeout_seconds
                        or self.settings.run_timeout_seconds)
        job = self.build_job(run, manifest, api_token=api_token,
                             sa_identity=sa_identity, run_token=run_token, pod_sa=pod_sa,
                             session_token=session_token, dev=self._is_dev(manifest))
        await self._audit_secret_access(run, manifest)
        await asyncio.to_thread(self.batch.create_namespaced_job, self.settings.k8s_namespace, job)

    async def _audit_secret_access(self, run: Run, manifest: Manifest) -> None:
        """Record the k8s secrets this run's pod is granted: the base claude
        credential (only in legacy direct-mount mode — with a claude-proxy the
        pod never sees it) plus the bound manifest/skill secrets. Best-effort —
        auditing must never block a launch."""
        if self.sf is None:
            return
        base = (["codex-credentials"] if manifest.runtime == "codex"
                and not self.settings.codex_proxy_url else
                ([] if self.settings.claude_proxy_url else ["claude-credentials"]))
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
