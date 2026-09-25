---
name: platform-orientation
description: Locate the Agent Platform's current source of truth before changing agents, Apps, Tools, permissions, or deployment behavior.
---

# Platform orientation

Read the project's `AGENTS.md` memory index, then the detailed memory linked for the part you are changing. Treat memory as history: verify today's code, database state, and deployment before relying on it.

Use `docs/building-blocks/` for current user-facing contracts and `docs/design/` for the reasoning behind them. Agent definitions and grants live in Postgres; capability code, Tool declarations, and skills live in Git. A domain App may keep a reviewed service or specialized UI even when its collection and page definitions move to the database.

Identify the responsible API, broker, runner, and UI boundary before editing. A platform Tool grant, a secret binding, and a skill assignment are separate decisions. A skill never grants a Tool or secret.
