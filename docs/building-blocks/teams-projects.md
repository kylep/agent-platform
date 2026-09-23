# Agent types, Teams, and Projects

An agent's **Type** describes why it exists. A **Persona** is a continuing
identity shaped by its conversations and memories. A **Worker** specializes in
a job. Type changes how the Agents page groups agents; it does not change the
runtime, model, role, tools, or permissions. **System agent** is a separate
lifecycle flag for platform-managed work, so system agents have their own
section below Personas and Workers.

**Teams** collect agents who collaborate. Each team gets a private Relay group
with its members added automatically. Edit membership on **Agents → Teams &
Projects**; team membership and the group's agent roster stay in sync. Add
people by username in the team's Settings to give them access to that private
room. A human
can write `@team:slug` in a Relay room to summon enabled team agents who are
already members of that room. The usual Relay hop, budget, cooldown, and
membership checks still apply. Agents cannot use team mentions to summon an
entire group. Team rooms retain their own history when archived.

**Projects** connect work over time, potentially across Teams and agents.
Membership is independent of Team membership. In Relay, choose the Team and
Project on a room to tag future turns. Runs inherit that context from the room
or their parent run; their prompts include a short Team/Project block. The
context is frozen on each Run for accurate history. An agent's memory stays
private to that agent, while notes saved during a scoped run inherit its Team
and Project tags. The memory tool can filter reads to the current scope.

The platform includes Project lookup guidance in a scoped run's context,
including resumed conversation turns. An agent can search Relay with the
Project slug and specific terms, then read relevant rooms or threads. Results
are limited to rooms the caller can already read; Project membership alone
does not open a private room. Old untagged rooms, runs, and memories remain
available without guessed assignments.

The `#eng` ticket channel is still the coding workroom; ticket prefixes such
as `ENG-12` are separate from Projects. The coding worker is named `coder`.
