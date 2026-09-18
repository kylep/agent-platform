import { Link } from "react-router-dom";
import { Chip } from "@ap/ui/chip";
import { asList, type AgentSummary, type CronEntry, type WebhookEntry } from "../../api";
import { cronTitle, isSingleExpression, useCronPreview } from "../../lib/cron";
import { Face } from "../relay/Face";

// One agent as a card (docs/design/23): its face — the picture when it has
// one — the name, the description, and the same readiness chip and schedule
// line the table row carries. The readings of a row live here so the card and
// the table cannot drift: whichever the reader picks, "blocked" means the
// same thing.

// The cron summary: the API may pre-render one, else it's the agent's own
// cron entrypoints (its row is the source of truth — docs/design/15).
// `asList` because the entrypoints blob comes back unvalidated — a warped row
// must cost this agent its schedule cell, not the whole listing.
export function scheduleOf(a: AgentSummary): string {
  if (a.schedule) return a.schedule;
  return asList<CronEntry>(a.entrypoints?.crons)
    .map((c) => c?.schedule).filter(Boolean).join(", ");
}

// The declared webhook paths, same defensive read as the crons above.
export function webhooksOf(a: AgentSummary): string[] {
  return asList<WebhookEntry>(a.entrypoints?.webhooks)
    .map((w) => w?.path).filter((p): p is string => typeof p === "string" && p !== "");
}

// The schedule cell. A hook per row, so each cell asks the platform what its
// own cron means — the descriptions are cached by expression, so a listing of
// agents on the same schedule costs one request, not one per row.
export function CronCell({ schedule, zone }: { schedule: string; zone?: string }) {
  const preview = useCronPreview(isSingleExpression(schedule) ? schedule : "", zone, 0);
  return <code className="cron" title={cronTitle(preview, zone)}>{schedule}</code>;
}

// Blocked = unmet required secret (fix the secret); quarantined = broken
// definition (fix the agent). Quarantine wins: a row that cannot be read has
// no readiness to report.
export function AgentStatusChip({ agent: a }: { agent: AgentSummary }) {
  return a.quarantined
    ? <Chip variant="danger" title={a.error ?? "Quarantined"}>quarantined</Chip>
    : a.blocked
    ? <Chip variant="danger" title={a.blocked_reason ?? "Blocked"}>blocked</Chip>
    : a.enabled === false
    ? <Chip variant="warn" title="Disabled — the definition stays, runs are rejected.">disabled</Chip>
    : <Chip variant="ok">ok</Chip>;
}

export function AgentCard({ agent: a, jobs }: { agent: AgentSummary; jobs: number }) {
  const schedule = scheduleOf(a);
  const hooks = webhooksOf(a);
  const href = `/agents/${encodeURIComponent(a.name)}`;
  return (
    <article className="agent-card" data-agent={a.name}>
      <Face participant={`agent:${a.name}`} face={a.face} size={96} />
      <div className="agent-card-body">
        <div className="agent-card-head">
          <Link to={href} className="agent-card-name">{a.name}</Link>
          <AgentStatusChip agent={a} />
        </div>
        {a.description
          ? <p className="agent-card-desc muted" title={a.description}>{a.description}</p>
          : <p className="agent-card-desc muted">No description.</p>}
        <div className="agent-card-foot muted">
          {schedule
            ? <CronCell schedule={schedule} zone={a.entrypoints?.timezone} />
            : jobs > 0
            ? <Link to={`${href}?tab=schedules`}>{jobs} job{jobs > 1 ? "s" : ""}</Link>
            : <span>no schedule</span>}
          {hooks.length > 0 && (
            <span title={hooks.map((p) => `POST /api/webhooks/${p}`).join("\n")}>
              webhook ✓
            </span>
          )}
        </div>
      </div>
    </article>
  );
}
