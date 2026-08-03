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

## Original scope (pai migration) — CLOSED 2026-08-03

**Archived (Kyle's call: "archive the pai migration, its done").** The v1
stack's copy is committed to `multi-sandbox` (4487f43) and multi PR #556
removes it from `multi` — Kyle merges that PR (branch protection requires his
approval; automation can't self-approve). Nothing else remains: the six
dropped workloads are written down below, `news` and `pai` are the ported
survivors.

State as verified 2026-07-29:

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

**DECIDED 2026-07-30 (Kyle): descope + archive, don't port.** "archive them,
i'll make something new if needed." The six remaining v1 workloads
(`seoBot`, `paiSelfImprover`, `paiMemoryBackup`, the three RSS crossposters,
`paiWeeklyHoroscopes`, `autolearn`) are **dropped**, not ported. The entire dead
pai-m1 stack (`multi/infra/ai-agents` — agent workloads + its cluster infra:
argocd/traefik/vault/cloudflared/openobserve, all dormant, pai-m1 no longer
resolves) is being archived to `multi-sandbox` per the repo-roles policy
(migrate, don't delete — history stays in multi). The one real secret in there,
`vault/gcp-credentials.json`, is gitignored/untracked and is NOT migrated.
`news` (journalist) and `pai` (pai-responder) remain the only ported workloads.

## Done when

~~Either every v1 workload is running, or the dropped ones are written down and
`multi/infra/ai-agents` is archived.~~ **Met (2026-08-03):** workloads formally
dropped (above); the archive copy is in `multi-sandbox`; multi PR #556 deletes
the tree from `multi` on Kyle's merge.
