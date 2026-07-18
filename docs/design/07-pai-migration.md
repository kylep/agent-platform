# Milestone 07 — pai Migration

Move the real workloads from `multi/infra/ai-agents` onto the platform
and retire the v1 stack.

## Scope

- **Inventory:** map each v1 workload (journalist, seo-bot,
  pai-responder, self-improver, crossposters, memory backup) to a
  platform agent + manifest; note which need skills that don't exist yet.
- **Port order:** lowest-risk cron agents first (journalist), Discord
  responder last (it's interactive and load-bearing).
- **Feature gaps:** whatever the ports surface (long-running listeners
  vs run-per-message for pai-responder is the known hard one — likely
  needs a resident-agent or fast-dispatch pattern decided here).
- **Retirement:** v1 stack decommissioned per workload as its
  replacement proves out over a burn-in window; multi wiki updated to
  point here.

## Done when

pai-m1's agent workloads are off the old stack, the NUC runs them all on
the platform, and `multi/infra/ai-agents` is archived per the repo-roles
policy.
