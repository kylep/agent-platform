# 27 — Invocation contracts and focused agents

Agent definitions describe a durable worker. Invocations describe one piece of
work. The platform keeps those two records separate so several triggers can use
one agent with different prompts and models without cloning its identity.

## Frozen runs

Every run freezes the complete validated agent definition before it enters a
runner pod. The run records the requested model override, the effective model,
the runtime, the definition version, and the definition snapshot. A retry reads
that snapshot rather than today's agent row. The runner-facing `agentdef`
endpoint reads the same snapshot, so its prompt and grants agree with the
dispatcher and launcher even when an administrator edits the live definition
mid-run.

Model overrides are part of the common invocation contract. Manual runs,
declared crons, declared webhooks, and Scheduled Jobs may request one. An empty
override means the agent default. Webhook request bodies cannot choose a model;
only the webhook definition can. An agent-authenticated caller cannot override
another agent's model through the manual run API.

## Platform-managed is one policy

`system` means platform-managed lifecycle: the definition is seeded by the
platform and cannot be deleted through the normal API. It does not implicitly
grant credentials and it does not decide Relay behavior.

`responds_to_all` independently controls whether a human `@all` includes the
agent. Direct mentions still work. Run credentials are derived from explicit
capabilities (`can_invoke` and platform-tool grants), while `role` continues to
select the execution profile. Existing system agents migrate to
`responds_to_all: false`, preserving their former room behavior without making
that behavior a hidden consequence of lifecycle ownership.

## Consolidation rules

Different trust boundaries remain different agents. `news` gathers hostile web
content while `news-librarian` answers from the sanitized archive; `artist` and
`codex-artist` spend different provider allowances; `engineer` and `qa` have
different publication authority. Those definitions stay separate even when
they sometimes use the same model.

The legacy `platform-coder` path is replaced by the `engineer` Workbench.
Skill and tool wizards dispatch scoped Workbench runs, so generated changes use
the same branch, verification, path-policy, and publication contract as other
code work. The old definition is disabled by migration rather than silently
accepting new work.

`stockmarket-data` remains a focused executor until deterministic Scheduled Job
actions exist. Folding its `prices` grant into the editorial `stockmarket`
agent would widen the latter's authority and still spend an LLM turn to call a
deterministic program. A later action-job design should invoke the reviewed
tool through the executor, record an action-run row, and preserve the same
audit and retry semantics before that agent is retired.

The shipped demo agent is disabled on existing installations. It remains a
recoverable row with history, but no longer consumes room mentions or accepts
runs by default.

Room-wide broadcasts target the conversational assistant, not every installed
worker. Focused agents, including `artist` and the full-workspace `engineer`,
set `responds_to_all: false`; direct mentions, assignments, schedules, and
webhooks still reach them. This avoids turning a standup into image spend or a
development pod launch while keeping lifecycle ownership independent.

The migration also removes ambient participant grants from focused pipeline
workers. `news` receives no platform API tool, `news-librarian` receives only
`query_app`, `running` receives `strava` and `query_app`, `stockmarket` receives
`index_movers`, and `stockmarket-data` receives `prices`. Result-topic delivery
is performed by the recorder and needs no agent-held Relay, Tickets, Wiki,
quota, or artifact authority.
