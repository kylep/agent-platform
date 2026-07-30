# 08 — Daily news & prompt-injection hardening

Ports multi's daily "journalist" onto the platform as a scheduled job, and — the
substantive part — does it **without handing an untrusted-input agent the keys
to the platform**. The design generalizes into a reusable pattern for any agent
that reads the open web.

## The problem: the lethal trifecta

An agent that (1) ingests untrusted content, (2) can read secrets/private data,
and (3) can exfiltrate externally is exploitable by a single poisoned page — the
"lethal trifecta." A naive news agent has all three: it reads arbitrary web
pages, it needs Discord + memory (credentials in its pod env), and runner pods
have open HTTPS egress. Its only defense would be a prompt saying "treat content
as untrusted," which is **not a security boundary**.

## The design: privilege separation

Split the job into three roles so **no single component holds all three legs**:

```
morning-news Job (cron)
      │  fires  (run.requested → run.inbound → materialize → dispatch)
      ▼
┌──────────────────┐   digest JSON as its run RESULT
│ GATHERER  (agent)│ ───────────────────────────────┐
│ WebSearch/Fetch  │                                 │  [Kafka: run.transcript]
│ NO creds/Bash    │                                 ▼
└──────────────────┘         ┌──────────────────────────────────┐
  untrusted web in,          │ NEWS PROJECTOR (recorder)         │ trusted code
  nothing to steal           │ parse+validate JSON · dedup vs    │ (not an LLM)
                             │ shared_news · sanitize · format   │
                             └───────────────┬──────────────────┘
                                             │ discord.channel.post {channel,text}
                                             ▼  [Kafka]
                             ┌──────────────────────────────────┐
                             │ CONNECTOR  (poster)               │ holds bot token
                             │ posts to #news as Pai             │ never sees raw web
                             └──────────────────────────────────┘
```

| Component | Untrusted in | Secrets | Exfil | Safe because |
|---|---|---|---|---|
| Gatherer | web | — | WebFetch | nothing to steal; no Bash/Read → can't reach env or `/secrets` |
| Projector | digest JSON | DB write | — | deterministic code, not an LLM — can't be "instructed"; validates + sanitizes |
| Connector | projector text | bot token | Discord | its input is platform-generated sanitized text — no injection vector |

A compromised gatherer's worst case is a garbage digest → the projector
sanitizes it (mentions defanged, schema enforced) → at most an odd `#news` post.
No credential theft, no shell, and the bot token never enters a runner pod.

## Runner: least-privilege tools (`services/runner/runner.py`)

`_permission_args()` replaces blanket `bypassPermissions` for credential-less
agents: it passes `--allowedTools <the agent's declared tools>` (run unattended,
no prompt) and `--disallowedTools Bash Read Edit Write NotebookEdit` (removed
from the model's context). So the gatherer — `tools: WebSearch, WebFetch`, no
API token — can search/fetch but literally cannot Bash or read files. Trusted
agents (an injected `AP_API_TOKEN` → `can_invoke`/`memory`/`system`) still get
`bypassPermissions`; self-edit still gets `acceptEdits`.

## Projector (`agentplatform/newsprojector.py`, wired in `recorder.py`)

Runs when a run of `settings.news_gatherer_agent` produces a successful `result`
frame. `parse_digest` extracts the JSON (tolerating fences/prose; unparseable →
posts nothing). Items are deduped against the server-owned `shared_news` table
(so the gatherer needs **no** memory/token), `sanitize` defangs Discord mentions
and strips mention tokens, `format_post` groups by section, new URLs are
recorded and records >`news_retention_days` pruned. It publishes
`discord.channel.post`; the connector delivers it.

## How agents connect — two patterns

- **Direct (agent-invokes-agent):** an agent with `can_invoke: true` gets a
  per-run **operator** token (`AP_API_TOKEN`) and calls `POST /api/runs`.
  `create_run` derives `trigger="agent"`, `parent_run_id`, and `depth` from the
  parent run server-side; `depth > max_run_chain_depth` (5) is rejected. Good
  for trusted orchestration.
- **Event handoff (what news uses):** the untrusted agent holds **nothing** and
  simply emits a result; trusted platform code (the recorder projector) carries
  it forward over Kafka. Use this whenever an agent touches untrusted input —
  giving it an invoke token would re-arm the trifecta.

## Approval gate — built then removed (2026-07-30)

A human approval gate ("Pending News" — hold each digest for approve/reject
before posting) was shipped and then **removed the same day**. It was never a
safety control: by the time a digest reaches this stage it is already bounded,
sanitized text from a privilege-separated pipeline (the trifecta is broken), so
the gate only added *curation* — and curation that needs standing human
attention is worthless when nobody gives it (digests piled up unreviewed, and
because `record_shared` only fired on approval, the dedup ledger went dormant).
Same reasoning that retired sync-signing: don't keep a control that depends on
attention you won't spend.

Now the projector posts directly and deterministically: `project()` dedups the
gatherer's digest against `shared_news`, sanitizes, records what it posts, and
publishes `discord.channel.post`. A bad post is a mildly-wrong message in the
owner's own channel — low cost, reversible by deleting it. The
`build_candidate`/`record_shared` split remains internal to `project()`.

Dedup keys on a **canonicalized URL** (`_norm_url`): lowercased scheme+host,
fragment and tracking params (utm_*, fbclid, …) stripped, trailing slash
trimmed — so the same story returning with a tracking tag is recognized as
already-shared. Records prune after `news_retention_days` (14).

## Deliberately out of scope (documented residuals)

- ~~The **shared Claude token** is still mounted in every runner pod.~~
  **Resolved 2026-07-30:** the token is brokered out of runner pods entirely —
  only the claude-proxy holds it. The enforced trifecta break
  (`Bash`/`Read`/etc. self-edit-only regardless of the manifest) remains the
  first line. See [09](09-token-brokering.md).

See also the dated spec: `docs/superpowers/specs/2026-07-29-news-privilege-separation-design.md`.
