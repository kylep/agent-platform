# Agent Types, Teams, and Projects

Status: shipped (2026-09-23). This design extends the
DB-first agent, Relay, and memory systems in designs 15, 19, and 31.

## Why these are three different things

**Type** describes what makes an agent useful: a `persona` accumulates an
identity and relationships across activities; a `worker` specializes in a
job. Type is separate from execution `role`, runtime, grants, and the `system`
lifecycle flag. It does not silently grant tools or alter permissions. Agents
default to `worker`; known companions can be explicitly classified `persona`.
The agent list groups Personas, Workers, then System agents. `system` wins the
display grouping so each agent appears once.

**Team** is a durable group of agents that work together. A team owns one
closed Relay group. Its agent membership is the roster for that group; human
participants may also join. `@team:<slug>` summons eligible team members
already admitted to the current room, using the existing Relay hop, budget,
cooldown, and coalescing guards. Agent-authored team broadcasts are refused,
as agent-authored `@all` is. Team membership never grants entry to an unrelated
private room.

**Project** is a bounded piece of work or life, such as a TTRPG playthrough or
marathon preparation. Agents may join many projects; a project may optionally
belong to one team. Conversations, runs, and private memories carry nullable
team/project provenance. Ticket prefixes remain properties of Relay channels;
they are not reinterpreted or migrated to Project.

## Context and retrieval

Every scoped run receives a short, structured Team/Project context block with
names and descriptions. The run freezes the scope of the conversation or
invocation that created it; child runs inherit that scope unless an authorized
caller explicitly chooses another. A resumed CLI session gets the current
scope block again, because its saved session may describe an older project.

An agent's memory namespace remains private even if several agents share a
project. Memory read/save can filter and tag by team/project without exposing
another agent's notes. Both `/api/memories` and the actual executor memory
tool need the same semantics. Conversation lookup by project is read-only,
bounded, and checks Relay membership for every title, snippet, count, and
transcript. A globally installed project-context skill explains how to find
relevant conversations and cite them; it cannot confer access by itself.

## Identity rename and migration

`engineer` becomes `coder`, keeping one identity rather than creating a second
agent. The migration must run once, refuse a name collision, and preserve
definition/version history, memories, runs, jobs, ticket assignments, Relay
membership and authorship, credentials, and live tool grants. Frozen historical
snapshots and prose remain historical. All hardcoded agent references (including
the skill/tool creation wizards and QA prompt) change to `coder`.

## Delivery and acceptance

1. Add Type and grouped agent UI; create Teams and Projects with membership
   management; safely rename `engineer`.
2. Connect Teams to closed Relay groups and guarded team mentions. Scope
   conversations and runs to projects/teams, with visible context in UI and
   prompts.
3. Scope memory and add project conversation lookup plus an installed skill.

Use a one-time schema/backfill migration with idempotence and collision tests;
focused Relay router tests must prove broadcasts cannot bypass existing guards.
Verify private memory and conversation access with different agent identities.
Live verification uses disposable scopes before assigning existing agents.
At 10% weekly Codex allowance remaining, finish the current coherent slice,
record remaining acceptance criteria as Tickets, and pause.
