# Milestone 07 — Conversations & Kafka Foundation (reframed from pai Migration)

**This milestone was reframed mid-flight and the doc did not keep up.** Read
this section before trusting anything below it.

M07 was originally "move the real workloads from `multi/infra/ai-agents` onto
the platform and retire the v1 stack" (the scope preserved verbatim under
[Original scope](#original-scope-pai-migration-still-open)). In practice, the
first workload we reached for — the interactive Discord responder — turned out
to need a conversation model the platform didn't have. That prerequisite grew
into the milestone, shipped under M07's number, and the migration itself was
never finished or formally descoped. Recorded here so it stops being invisible.

## What actually shipped (verified live)

- [x] **Event envelope + DLQ + event-sourced ingress** — every inbound message
      is an enveloped, idempotent Kafka event; malformed or unroutable ones
      dead-letter visibly rather than vanishing.
- [x] **`Conversation` entity + UI** — the platform owns conversation identity
      and history; a connector owns none of it.
- [x] **Discord connector** — a mention opens a thread, the thread *is* a
      Conversation, and the `pai` agent replies in it. Sole holder of the bot
      token; speaks by consuming `discord.channel.post`.
- [x] **Connector registry** — Slack is registered `implemented: false` and
      shows as a greyed "NYI" chip; `services/connector-slack/README.md` records
      the exact two-topic contract a future connector implements.

## Original scope (pai migration) — STILL OPEN

Verified 2026-07-29:

- The v1 code still sits at `multi/infra/ai-agents`, last touched 2026-07-04
  (a dependency-vulnerability sweep — no feature work since).
- Its CronJobs all default to `enabled: false`, there is no `ai-agents`
  namespace and no CronJob anywhere on the NUC, and the old `pai-m1` host no
  longer resolves. So **v1 is dormant, but not archived** — and the workloads
  it used to run are simply *off*, not replaced.

| v1 workload | Status |
|---|---|
| journalist (3×/day) | **Ported** → `news` agent, once each morning, privilege-separated (see [08](08-news-and-injection-hardening.md)) |
| pai-responder | **Ported** → `pai` agent + Discord connector |
| seoBot | Not ported |
| paiSelfImprover | Not ported |
| paiMemoryBackup | Not ported |
| crossposters (tweet / bluesky / mastodon RSS) | Not ported |
| paiWeeklyHoroscopes | Not ported |
| autolearn | Not ported |

**Open decision (needs a human):** port the remaining six, or formally descope
them and archive `multi/infra/ai-agents` to `multi-sandbox` per the repo-roles
policy. They are real functionality that is currently switched off rather than
replaced, so letting the doc imply "migrated" would be a lie either way.

## Done when

Either every v1 workload is running on the platform, or the ones we've chosen to
drop are written down as dropped — and `multi/infra/ai-agents` is archived per
the repo-roles policy.
