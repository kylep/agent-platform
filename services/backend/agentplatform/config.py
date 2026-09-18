from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AP_")
    db_url: str = "sqlite+aiosqlite:///:memory:"
    kafka_bootstrap: str = "localhost:9092"
    k8s_namespace: str = "agent-platform"
    runner_image: str = "agent-platform-runner:dev"
    # The synced git checkout itself (docs/design/10's building blocks live in
    # subdirectories of it). Agents used to be one of them and gave this
    # setting its old name, `agents_root`; definitions are rows now
    # (docs/design/15), and what is left needing the checkout is the docs the
    # Help pages serve and the sha /api/sync-status reports.
    checkout_root: str = "."
    skills_root: str = "./skills"
    secrets_root: str = "./secrets"
    reports_root: str = "./reports"
    apps_root: str = "./apps"
    tools_root: str = "./tools"
    # SPIRE mTLS (docs/design/13 B). When enabled, run pods that talk to the
    # MCP broker get a ghostunnel-client native sidecar carrying the pod's
    # SVID, and AP_MCP_URL points at its localhost listener instead of the
    # broker service. Flipping this off (helm --set spire.enabled=false) is
    # the break-glass: everything reverts to plain in-cluster HTTP + netpol.
    spire_enabled: bool = False
    ghostunnel_image: str = "docker.io/ghostunnel/ghostunnel:v1.11.2"
    spiffe_trust_domain: str = "pai"
    spiffe_workload_socket: str = "unix:///spiffe-workload-api/spire-agent.sock"
    # The broker's ServiceAccount (release-name prefixed) — what run-pod
    # tunnels pin as the expected server identity.
    broker_service_account: str = "ap-mcp-broker"
    mcp_broker_mtls_target: str = "agent-platform-mcp-broker:8443"
    agents_volume_claim: str = "agent-definitions"
    session_secret: str = "dev-insecure"
    global_concurrency: int = 3
    run_timeout_seconds: int = 1800
    # GC finished run Jobs + pods this long after they finish (k8s TTL
    # controller). Run history lives in postgres, so pods are disposable.
    run_ttl_seconds: int = 3600
    # Loop guard for agent-invokes-agent: a run whose depth would exceed this
    # is rejected. depth 0 = human/schedule/webhook; each nested invoke +1.
    max_run_chain_depth: int = 5
    # Pre-flight credential gate: once the Claude token is known-bad (a run hit
    # a 401), the dispatcher rejects further runs up front instead of launching
    # doomed pods. To detect recovery it lets ONE run through this often (a
    # circuit-breaker half-open probe), so a fixed token self-heals. <= 0
    # disables the gate (runs always dispatch).
    credential_recheck_seconds: int = 300
    # Secret-validation heartbeat (docs/design/10): the dispatcher re-runs each
    # verifiable secret's probe/script this often and records the status, so
    # `valid` can't go stale-green. <= 0 disables the heartbeat.
    secret_verify_interval_seconds: int = 600
    # Default transcript retention: prune run_transcript_events older than this
    # many days (Run metadata/summary is kept). Per-agent manifest override wins;
    # <= 0 disables pruning (keep forever).
    transcript_retention_days: int = 30
    # Max size of a stored Claude session blob (docs/design/14). A PUT above
    # this clears the blob instead of keeping it: a stale/oversized session is
    # worse than a clean reset to the text-replay fallback.
    session_blob_max_bytes: int = 8_000_000
    # A conversation turn's reply text and its terminal state arrive on separate
    # Kafka topics. If a finished turn still has no published reply this long
    # after finishing, the recorder's sweep publishes with what it has, so a lost
    # frame can't leave a thread waiting forever. <= 0 disables the sweep.
    reply_reconcile_seconds: int = 60
    # Self-hosting git target. git_remote_url is what the platform clones and
    # pushes to (a local bare repo in tests, the real repo over HTTPS in prod);
    # github_repo ("owner/name") is used for the PR API. Empty = self-edit off.
    git_remote_url: str = ""
    github_repo: str = ""
    default_branch: str = "main"
    # In-cluster API base URL injected into system-agent runs so they can call
    # the platform (e.g. the run summarizer annotating runs).
    api_internal_url: str = "http://agent-platform-api:8000"
    # Auth-injecting egress proxy for the Claude API (docs/design/09). When set,
    # runner pods get ANTHROPIC_BASE_URL=<this url> and a placeholder credential
    # instead of the claude-credentials secret mount — the real subscription
    # token lives only in the claude-proxy pod, which injects the Authorization
    # header on the way out. Empty = legacy direct mount.
    claude_proxy_url: str = ""
    # MCP broker service URL injected into token-bearing runs (the runner points
    # claude at it so agents get brokered API tools instead of a shell).
    mcp_broker_url: str = "http://agent-platform-mcp-broker:8000/mcp"
    # Relay loop guards (docs/design/19). A run triggered by a message at hop h
    # posts its reply at h+1; an agent-authored message at this hop can summon
    # nobody, which is where a two-agent ping-pong stops. A human mention starts
    # a fresh chain at hop 0, so a person can always continue a paused thread.
    relay_max_hops: int = 4
    # Spend caps on mention-triggered runs, counted from relay_invocations so
    # they survive a restart. Over budget the router suppresses and says so once
    # per channel per hour, rather than once per suppressed message.
    relay_channel_invocations_per_hour: int = 30
    relay_global_invocations_per_hour: int = 120
    # An agent mentioned by another agent within this long of its own last reply
    # in that channel gets a coalesced wake instead of a second run: three
    # mentions during one reply become one follow-up, never three.
    relay_agent_cooldown_seconds: int = 20
    # How many recent channel messages a mention run is shown as context.
    relay_context_messages: int = 30
    # Whether agent creation grants mcp__platform__relay. The grant is a real
    # row either way, so an admin can remove it per agent like any other.
    relay_default_grant: bool = True
    # Tickets (docs/design/20). Opening a ticket is cheap and permanent, so an
    # agent stuck in a retry loop can bury the board in noise no human asked
    # for: creation gets its own hourly cap per agent, counted from the ticket
    # rows. Moves, comments and assignments are NOT capped — they are how work
    # gets finished, and an assignment is already a Relay mention paying the
    # relay budget.
    tickets_agent_creates_per_hour: int = 20
    # "in progress and nobody has touched it in this long" — the board's stale
    # badge and the stats. Days, because a ticket is a unit of work and not of
    # execution: an agent can legitimately be mid-ticket overnight.
    tickets_stale_days: int = 3
    # Whether agent creation grants mcp__platform__tickets, same bargain as
    # relay_default_grant: on by default because an agent that cannot file what
    # it found leaves the finding in a transcript nobody reads.
    tickets_default_grant: bool = True
    # How many messages of a ticket's thread a summoned run is shown. Larger
    # than relay_context_messages: the thread IS the ticket's history, and a
    # hand-off that starts halfway through it repeats work already done.
    tickets_thread_context_messages: int = 40
    # Wiki (docs/design/21). A page is permanent and shared, so a looping agent
    # can rewrite the platform's own knowledge faster than anyone reads it:
    # writes get an hourly cap per agent, counted from wiki_versions. Humans are
    # not metered — a person editing 40 pages in an hour is a working afternoon.
    wiki_agent_writes_per_hour: int = 30
    # How many matching pages the `<wiki>` prompt block offers a run. Small on
    # purpose: the block is summaries and slugs pointing at the wiki tool, not
    # the wiki itself, and every page here is context every run pays for.
    wiki_prompt_pages: int = 5
    # Whether agent creation grants mcp__platform__wiki, same bargain as
    # relay_default_grant: knowledge nobody can write down stays in a transcript.
    wiki_default_grant: bool = True
    # Whether agent creation grants mcp__platform__get_quota_usage
    # (docs/design/22). On for the same reason the others are: an agent that
    # cannot see how much of the shared allowance is left spends it as if it
    # were infinite.
    quota_default_grant: bool = True
    # "nobody has touched this page in this long" — the gardener's stale list
    # and the stats. Long, because a page that is still true needs no edits;
    # stale means unreviewed, not wrong.
    wiki_stale_days: int = 30
    # Hard cap on a page body, in bytes. Every version stores the FULL body, so
    # this is what makes the history affordable — and a page past this size is
    # one nobody reads anyway, it wants splitting and a [[link]].
    wiki_max_body_bytes: int = 65536
    # Quota (docs/design/22). The shared secret the claude-proxy presents on
    # `POST /api/internal/quota` — the ONE credential that door accepts, since
    # nginx calls it with no session and no API key. Empty means the endpoint
    # refuses everybody (503): an API that cannot tell the proxy from anyone
    # else must not take usage reports from either.
    internal_secret: str = ""
    # The platform-wide floor between probes. A refresh arriving inside this
    # window of a fresh, non-stale snapshot is answered from the cache, so the
    # quota tool costs at most one probe per interval however many agents are
    # asking — the guard is the rate, not the caller's role.
    quota_refresh_min_seconds: int = 15
    # What the probe asks about. The cheapest current model, because the answer
    # is a header and the completion is thrown away.
    quota_probe_model: str = "claude-haiku-4-5"
    quota_probe_timeout_seconds: float = 20
    # Artifacts (docs/design/23). The bytes live in postgres, so both caps are
    # about the database and not the disk: one artifact is bounded the way a
    # session blob is (8 MiB), and the whole store is bounded because a looping
    # agent saving screenshots would otherwise grow the platform's own database
    # until the backup CronJob is the first thing to notice. A write past the
    # total is a 507 with a message the Studio shows.
    artifacts_max_bytes: int = 8 * 1024 * 1024
    artifacts_total_max_bytes: int = 2 * 1024 ** 3
    # A delete is `deleted_at` so a card naming a gone artifact still says
    # "deleted"; the dispatcher hard-deletes rows (and their bytes) this long
    # after, in the retention loop that prunes transcripts.
    artifacts_prune_days: int = 30
    # Whether agent creation grants mcp__platform__artifacts. On for the
    # reason relay's is: an agent that cannot keep a file describes it instead.
    artifacts_default_grant: bool = True
    # Image generation (docs/design/23). Every generation goes through
    # `POST /api/artifacts/generate`, which runs the internal `image_gen` tool
    # on the executor — the one service that holds provider keys.
    executor_url: str = "http://agent-platform-tool-executor:8000"
    # An image costs real money, so a looping agent is a bill and not just
    # noise: generations get an hourly cap per agent, counted from the
    # artifact rows. Humans are not metered — a person iterating in the Studio
    # is the point of the Studio.
    image_gen_agent_per_hour: int = 10
    # The whole platform's daily spend, from `meta.cost_usd` (the registry's
    # estimate), for everyone: past it the route answers 402 and `#art` says
    # so once a day. The day turns at midnight in `local_timezone`.
    image_gen_daily_usd: float = 5.0
    # The platform's wall clock: the zone the seeded jobs fire in, and the one
    # "today" is measured in wherever the platform counts a day. Not the
    # scheduler's per-job field — that is each job's own.
    local_timezone: str = "America/Toronto"
    # (The news pipeline settings are gone: news presentation lives in the news
    # APP now — the recorder just honors each manifest's `result_topic`.)

@lru_cache
def get_settings() -> Settings:
    return Settings()
