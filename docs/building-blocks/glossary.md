# Glossary

Every other page assumes these words. This one defines them once, so no doc
has to stop and explain "the dispatcher" again.

## The reference deployment

The platform is a Helm chart (`charts/agent-platform`) that can run on any
Kubernetes cluster. The deployment the docs describe — the one every example
command is written against — is a **single-node k3s cluster on a home Intel
NUC named `pai`**, installed as Helm release **`ap`**, with the UI on
`http://pai:8090`. When a doc says "the cluster", that is what it means; when
it says `ap-api` or `ap-dispatcher`, that is the release name `ap` plus a
component name.

## The workloads

Every long-running piece of the platform. All of these are Deployments in the
`agent-platform` namespace; agent runs themselves are short-lived Jobs.

| Name | Image built from | What it does |
|---|---|---|
| **api** | `services/backend` | The HTTP API and the source of truth for authorization. The UI, the agents, and the broker all talk to it. |
| **dispatcher** | `services/backend` | Consumes run commands from Kafka and launches a Kubernetes Job per run. Also runs the reconciliation heartbeat that provisions declared infrastructure (secrets, app and tool databases). |
| **recorder** | `services/backend` | Consumes transcript events from Kafka and persists runs, events, and metrics to Postgres. |
| **runner** | `services/runner` | The image an agent run *is*: it fetches the agent's definition from the API and mounts its skills, runs Claude Code inside the pod, and streams every event back to Kafka. One pod per run, then gone. |
| **web** | `services/web` | The React UI plus the nginx that serves it, terminates the login session, and proxies `/api` and `/apps/<name>/`. The only LAN-facing service. |
| **mcp-broker** | `services/mcp-broker` | The single MCP server agents talk to. It verifies who is calling and that the caller's definition declares the tool, then performs the call itself — agent pods never hold platform credentials. See [tools.md](tools.md) and [security.md](security.md). |
| **mcp-facade** | `services/mcp-facade` | The MCP server *external* clients talk to (Claude Code on a laptop), served at `/mcp` through web's nginx. Its tools are generated from the API's OpenAPI document, and it forwards the caller's own `Authorization: Bearer ap_…` on every call — and *only* that header, never a session cookie — so it holds no credential, grants no authority, and the API's role ladder decides everything. A request with no `Authorization` header at all is refused at the door, so a keyless client cannot even read the tool list. Distinct from the broker, which scopes tools to an in-cluster run's grants. |
| **tool-executor** | `services/tool-executor` | Runs a custom tool's `run.py` in a locked-down subprocess with a minimal environment. The broker is its only client, and it is the platform's single point of third-party network egress. |
| **claude-proxy** | stock nginx + a config in the chart | Holds the Claude API credential and injects it into requests from runner pods, so the token never lands in an agent's pod, and reports the usage headers Anthropic returns to the API — which is how the platform knows its own [quota](quota.md). |
| **agents-sync** | stock `alpine/git` | Keeps the **synced checkout** (below) up to date with the git repository. |
| **connector-discord** | `services/connector-discord` | Bridges a Discord channel to the conversation API, so a chat message can start a run. |
| **app pods** | `apps/<name>/` | Full applications built on the platform ([apps.md](apps.md)), each with its own Postgres schema. |

## Vocabulary

- **Synced checkout** — the shared volume holding a clone of this repository,
  refreshed by agents-sync. It is what the API reads when it lists skills,
  tools, secret declarations, reports, apps, and these help pages: edit a file
  in git and the running platform picks it up on the next sync, with no
  redeploy. Agent *definitions* are the one building block that no longer
  lives here — they are rows in Postgres, see [Agents](agents.md).
- **Change loop** — the standard way *capability* changes land: an edit in
  the UI (or a wizard) has a coding agent open a **pull request** on a
  deterministic branch, which shows up under Changes for review. Nothing in
  `skills/`, `secrets/`, or `tools/` is edited in place in the cluster. See
  [changes.md](changes.md). Agent definitions do **not** use this loop —
  they write straight to their row and get their own audit trail instead
  (the **change log**, `agent_versions`; see [Agents](agents.md)). Two
  different mechanisms, both append-only, easy to conflate: the change loop
  is pre-merge review for code/config in git, the change log is a post-hoc
  record of already-live database writes.
- **Manifest** — the small YAML file that declares a block to the platform:
  `tools/<name>/tool.yaml`, a skill's frontmatter. Manifests declare *what is
  wanted*; the platform converges the cluster toward it. Agents no longer have
  a manifest file — their equivalent fields (role, skills, secrets, grants,
  limits) live directly on the `agent_defs` row.
- **Readiness gate** — the check, run before any pod launches, that an agent's
  required secrets are actually present and verified. An agent failing it is
  **blocked** and its runs are rejected with the reason recorded. See
  [agents.md](agents.md) and [secrets.md](secrets.md).
- **DLQ** (dead-letter queue) — where a run goes when the *launch* itself
  failed, rather than the agent. The DLQ page in the UI lists them for replay.
- **MCP** (Model Context Protocol) — the open protocol Claude Code uses to
  call tools hosted outside its own process. It is how agents reach the
  mcp-broker.
- **Platform agents** — agents that exist to operate the platform itself and
  are marked `system: true`: **platform-coder** (writes the pull requests
  behind every UI-driven *capability* change — skills, tools, secrets;
  agent-definition edits no longer go through it), **run-summarizer**
  (annotates finished runs), **health-monitor** (checks platform health and
  alerts), **change-summarizer** (explains pull requests in the Changes UI).
- **Relay** — the agent messenger: the rooms humans and agents talk in, the
  `@mention` that summons an agent, and the router that decides whether the
  summons happens. The block is [relay.md](relay.md); the design record is
  `docs/design/19-relay-agent-messenger.md`.
- **Channel** — a Relay room with a `#name`, a topic and members. An **open**
  channel (`#general`, `#ops`, `#standup`) has every human and every enabled
  agent as a member implicitly, so it carries no membership rows; a closed
  channel or a **group** lists its members explicitly, and a mention of a
  non-member is dropped.
- **DM** — a Relay channel of `kind='dm'`: exactly two members, a human and an
  agent. What a [Conversation](conversations.md) is now.
- **Thread** — a reply chain hanging off one message. Every message carries
  `reply_to` (what it answers) and `thread_root` (the message that started the
  chain); the UI keeps replies out of the room and shows them in a pane beside
  it — the root says how many there are — and a summoned agent's answer is
  posted into the thread it was summoned from.
- **Hop** — how far a message is from the human who started it. A human or
  system message is hop 0, an agent's answer to it is hop 1, and at
  `relay_max_hops` (4) the router refuses to summon again and says so in the
  room. The fence that makes agent-to-agent conversation finite; a human
  message resets it to 0.
- **Wake** — a `relay_wakes` row recording "somebody mentioned you while you
  were busy". Mentions of an agent that already has a run in the room coalesce
  into one wake, and the agent gets a single follow-up run covering everything
  said since — three mentions during one reply become one run, not three.
- **Budget (relay)** — the per-hour ceiling on mention-triggered runs, per
  channel (30) and platform-wide (120), counted from `relay_invocations` so it
  survives a restart. Over budget, a mention is **suppressed**, recorded as an
  invocation with a reason, and surfaced on the Dashboard.
- **Bridge / binding** — a `relay_bindings` row mapping another chat app's room
  (`connector` + `external_ref`, e.g. a Discord thread or channel) to a Relay
  channel. The bridge is what makes a Discord message a Relay message and back;
  connector-discord is the only one built.
- **Face** — a participant's emoji on a hue-tinted disc. An agent's `icon` when
  it has one, otherwise derived from a hash of the name (as are humans' and
  bridged users'), so the same name looks the same everywhere forever without
  anybody picking colours.
- **Ticket** — one piece of work the platform is tracking: a title, a state
  (`open` → `in_progress` → `blocked`/`review` → `done`/`cancelled`), an
  assignee, and the Relay thread it is being discussed in. Agents open and move
  them through the `tickets` tool, humans through the board. The block is
  [tickets.md](tickets.md); the design record is
  `docs/design/20-tickets-agent-work-tracker.md`.
- **Project (tickets)** — a Relay channel with a ticket prefix. It is not a
  second kind of object: `#ops` with prefix `OPS` *is* the OPS project, its
  tickets live in it, and each one's card is a message in the room.
- **Key** — a ticket's permanent name, `PREFIX-n` (`OPS-12`). The prefix comes
  from the project channel's name, the number from a per-project counter, and
  neither changes for the life of the ticket — including after it closes.
  Case-sensitive: `ops-12` in a sentence is the word, not the ticket.
- **Page** — one subject written down in the [wiki](wiki.md): a slug, a title,
  a markdown body, tags, and a version that goes up on every write. Every write
  keeps the whole previous body, so a page's history is complete. Agents write
  pages through the `wiki` tool, humans through the editor; either way the page
  is the platform's shared, citable knowledge, as opposed to a
  [memory](memories.md), which is one agent's private note.
- **Slug** — a page's permanent name and its whole identity:
  `^[a-z0-9][a-z0-9-]{0,63}$`, which is at once the URL (`/wiki/deploying`) and
  the link target (`[[deploying]]`). A slug that is a near-miss is a second
  page about one thing, so the grammar is enforced rather than corrected.
- **Wiki-link** — `[[slug]]` in ordinary prose, rendered as a chip to that
  page wherever prose is rendered: a wiki page, a Relay message, a ticket's
  description. Inside code, a fence, a markdown link or a URL it is quoted text
  and stays as written. A wiki-link in a Relay message also counts as a
  **citation**, which is what a page's "cited in N messages" is.
- **Wanted page** — a slug something links to that nobody has written yet. It
  renders red, following it opens the editor already named after it, and the
  wiki's Wanted list is every one of them, most-linked first. A feature, not a
  broken link: it is how the wiki says what it is missing.
- **Librarian** — the `wiki` system agent. It answers `@wiki` from the pages
  rather than from memory, cites what it used as `[[slug]]`, and offers to
  write the page when the wiki cannot answer. The `wiki-gardener` job asks it
  every Sunday what has gone stale and what is still red.
- **Usage window** — one of the two rolling limits on the Claude
  subscription every agent runs on: a **5-hour window** and a **7-day window**,
  each reported as a percentage already spent across the whole platform and a
  time it resets. The sidebar's two bars are these; the block is
  [quota.md](quota.md). "Usage", not "rate limit" — a rate limit is what
  happens when a window is full.
- **Snapshot (quota)** — the single stored reading of both windows: the
  latest values, when Anthropic answered (`observed_at`), and which path saw
  them. The claude-proxy contributes one for free off every response it
  relays; a **refresh** is the platform spending the cheapest possible call to
  ask on purpose. A snapshot is **stale** once the earlier of its two windows
  has reset, which is what dims the bars.
- **Kyle (project owner)** — the sole operator of the reference deployment.
  Design docs quote him directly; those quotes are the historical record of a
  decision, not instructions to the reader.

## Where the design record lives

`docs/design/00-overview.md` indexes the numbered design documents. A doc that
refers to "design 12" means `docs/design/12-executable-capabilities.md`; the
number is stable, the file name may gain words. The design docs record *why* a
thing is the way it is; these building-block pages record *what it is now*.
