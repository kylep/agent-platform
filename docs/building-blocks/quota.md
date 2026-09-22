# Quota

**What:** how much of the shared Claude and Codex allowances are already spent
(`docs/design/22-quota-usage-bars.md`). The subscription that runs every agent
has two rolling limits — a **5-hour window** that refills several times a day
and a **7-day window** that does not — and the thin bars under "Agent
Platform" show Claude in blue/pink and Codex in green/teal. The number in
each bar is the percentage **used**, by everybody on this platform together:
every agent, every job, and the humans clicking around.

The point is that the allowance is shared and nobody could see it. Before
this, a window filling up showed as runs failing with an empty error and a
`rate_limit_event` buried in a transcript, Kyle learned the weekly number from
a terminal on his laptop, and an agent about to start a long sweep had no way
to ask whether there was room for it.

**Lives in:** platform Postgres — one row per provider, `quota_snapshot` and
`codex_quota_snapshot`, holding the
latest utilization and reset of each window, the unified `status`, every
`anthropic-ratelimit-unified-*` header verbatim, when Anthropic answered, and
which path saw it. One row, because the answer is "what is true now": history
is the `quota.events` Kafka topic (7-day retention), which carries an envelope
only when a number actually **moves**, so it reads as a burn-rate log rather
than a log of every API call.

## Where the numbers come from

Anthropic reports both windows on **every** response, and every one of those
responses already passes through a pod the platform owns: the
[claude-proxy](../design/09-token-brokering.md), the nginx that injects the
credential. It reads the usage headers off each upstream response, and a tick
every 5 seconds posts the newest reading to the API's internal endpoint with a
shared secret. That is the whole passive path, and it costs nothing — the
platform learns its own usage as a side effect of work it was doing anyway.

A **snapshot** is that stored reading. An **observation** is one set of header
values seen on one response. A **refresh** is the platform asking on purpose:
the cheapest Claude call that still carries the headers (a `count_tokens` on a
one-character message; a one-token completion if that comes back without
them), made by the API *through the same proxy*, so the token never leaves
where design 09 put it.

Codex exposes an authenticated usage document through the same ChatGPT OAuth
credential used for runs. The API asks the `codex-proxy` for that document over
an internal-secret route; the proxy refreshes OAuth when needed and returns no
credential material. Codex accounts may omit a window, so window identity is
derived from its reported duration and an absent window is omitted from the UI.

## What agents can do

Every agent holds one default-granted broker tool, `get_quota_usage`
(`mcp__platform__get_quota_usage`), with no arguments. It answers:

```
5-hour window: 22% used, resets in 3h 54m (2026-09-14 19:00 UTC).
7-day window: 81% used, resets in 3d 8h (2026-09-17 23:00 UTC).
Observed 2s ago (refresh). Status: allowed.
```

Above 90% on either window the answer adds a line telling the agent to defer
heavy work until the reset. An agent should call it **before committing to
something expensive** — a long research sweep, a big refactor, a batch of
subagents — and when the choice is between doing the thorough version now and
doing it after the reset. It is cheap to call on purpose: at most one tiny
probe, and calls arriving close together are answered from the last reading
instead of probing again.

"Default-granted" is the same bargain as [Relay](relay.md)'s,
[Tickets](tickets.md)' and the [Wiki](wiki.md)'s: while `quota_default_grant`
is on, agent creation adds the tool to the new agent's `platform_tools`, and
an admin can take it away through the normal grant path ([agents.md](agents.md)).
An agent that cannot see how much of the shared allowance is left spends it as
if it were infinite.

## The API

| Route | Who | What |
|---|---|---|
| `GET /api/quota` | readers and up, plus the participant role | Claude's backward-compatible snapshot plus a nested `codex` snapshot, each with `stale` and `age_seconds`. |
| `POST /api/quota/refresh` | same | Probe both providers and return their readings. `?provider=codex` refreshes only Codex, and `?provider=claude` only Claude. One unavailable provider does not erase the other's cached reading. |
| `GET /api/quota/events` | same | SSE: a frame whenever a number moves. |
| `POST /api/internal/quota` | the proxy's shared secret only | The passive report. No session and no API key reach it. |

The refresh is **coalesced and rate-limited platform-wide**, not per caller: a
second caller arriving during a probe waits for that probe's answer, and a
refresh arriving within `AP_QUOTA_REFRESH_MIN_SECONDS` of a fresh reading is
answered from the cache. However many agents ask at once, the platform spends
at most one probe per interval. That is also why the raw refresh route is not
offered by the [external MCP facade](../design/17-external-mcp-facade.md):
agents reach it through the tool, which is where the per-agent metering lives.

## Stale, and what a page load does

A snapshot is **stale** when the earliest window it describes has already
reset — the numbers are a description of a window that no longer exists. (A
reading with no reset time at all is thin, not stale; it has nothing to expire
against.) A stale bar dims, track and label together, so it reads as the last
thing Anthropic said rather than as a live number.

On load the sidebar reads `GET /api/quota`, fires **one** full refresh if what
came back is stale, and otherwise refreshes Codex alone. It then follows
`quota.events` over SSE, with a 60-second poll if the stream is down. While the
tab is visible it refreshes the token-free Codex usage reading every five minutes;
returning to the tab also refreshes it. This keeps a week-long Codex window from
looking current for days without spending Claude tokens.
Stream frames and the refresh's own answer always win; only a catch-up read is
ordered by `observed_at`, so a read that started before a refresh and landed
after it cannot walk the bars backwards. With nothing known at all the bars
render nothing — placeholder bars would be a claim about usage, and absence is
not.

## Settings

| Setting | Default | What it is |
|---|---|---|
| `AP_INTERNAL_SECRET` | none (required) | The one credential `POST /api/internal/quota` accepts. Empty means that door refuses everybody (503). |
| `AP_QUOTA_REFRESH_MIN_SECONDS` | `15` | The platform-wide floor between probes. |
| `AP_QUOTA_PROBE_MODEL` | `claude-haiku-4-5` | What the probe asks about — the cheapest current model, since the completion is thrown away and the answer is a header. |
| `AP_QUOTA_PROBE_TIMEOUT_SECONDS` | `20` | How long a probe may take before it is a 503. |
| `claudeProxy.quota.enabled` | `true` | Whether the proxy captures at all. Off: no Secret, no mount, no `AP_INTERNAL_SECRET`, and the internal route fails closed. |
| `claudeProxy.quota.apiUrl` | `http://agent-platform-api:8000` | Where the proxy posts its reports. |
| `quota_default_grant` | `true` | Whether agent creation grants `mcp__platform__get_quota_usage`. |

`AP_INTERNAL_SECRET` is required exactly like `AP_SESSION_SECRET` and lives in
the stored Helm values, never in git. The chart wires both halves of it —
the `<release>-internal` Secret the proxy mounts and the API's env — from that
one value, so neither half can be configured without the other.

**Rotating it:** change the value in the stored values and `helm upgrade`. The
proxy reads the secret from its mounted file **per request**, the same
property the Claude token has, so it picks the new value up on the kubelet's
volume sync with no restart; the API reads it from its environment, so the api
pods roll. Reports posted in the gap between the two are refused with a 401
and dropped, which costs a few seconds of lag and nothing else — the passive
path is idempotent and the next tick carries the current reading anyway.

## Two things it does not do

- **The bars can lag a response by up to 5 seconds.** The proxy writes each
  reading into a shared dict and a periodic tick posts it, because issuing the
  request from the response filter itself blanks the client's response on the
  njs the nginx image ships. A burst of responses collapses to its newest
  reading. This is the deliberate trade in `docs/design/22`.
- **The platform only learns from its own traffic.** Nothing else on the
  account — Claude Code on a laptop, the phone app — reports here, so if the
  platform is idle the snapshot ages until something refreshes it: a page
  load, an agent asking, or the next run's first response. The number is
  account-wide and correct when it was observed; `observed_at` is the part to
  read when it matters.
