# System agents

System agents implement platform capabilities. Their definitions are versioned
in code; their runs use the same dispatcher, Relay, Tickets and run credentials
as other agents. System ownership is separate from the persona/worker type.
User-created workers and personas remain database configuration.

The initial registry owns five workers: `health-monitor`, `change-summarizer`,
`run-summarizer`, `wiki`, and `codex-artist`. Their source and revision identify
the definition used by the platform. An unrelated historical `system` flag does
not automatically enroll an agent in this registry.

You can change operational settings: enabled state, runtime/model, concurrency,
timeout, transcript retention, quota thresholds, cron entries and timezone.
Prompts, tools, roles, external identities, webhooks and event subscriptions are
code-owned. The agent editor/API rejects changes to those fields, including
attempts to restore them from an old version. Code reconciliation preserves
supported operational settings and records definition changes in version history.
A name collision with a user worker is reported instead of overwriting that
worker. Removing a source disables its worker without deleting its history.

Health Monitor checks platform metrics and records actionable incidents as OPS
Tickets through its dedicated `health_incident` Tool. Each stable incident key
maps to one Ticket. Repeated observations update the evidence; recovery closes
the incident, and recurrence can reopen the same Ticket. This Tool is restricted
to an active Health Monitor run and never sends external messages.

Pai receives an internal assignment when available. Pending health requests also
appear in a bounded context block on her next invocation, so a missed or
suppressed wake does not lose the request. Pai decides whether and how to notify
you through her connectors and records that decision in the Ticket. Closing a
handled or dismissed Ticket stops reminders until an actual recovery and later
recurrence. A disabled Pai leaves a durable unassigned request for later review.

News, Running and Stockmarket continue saving their platform data and reports.
Their ingestion code no longer broadcasts those outputs to Discord. Personas
retrieve the outputs when communicating with you.
