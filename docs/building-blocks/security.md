# Security

**The one-sentence version:** agents never hold credentials or a shell —
everything an agent *does* goes through platform code that checks who is
asking, and everything it *knows* about you stays out of its reach.

## Why agents get no shell

An agent is a language model reading untrusted stuff — web pages, news,
chat messages. Anything it reads can try to talk it into misbehaving
(prompt injection). So the platform never gives a normal agent the three
things an attacker needs all at once: untrusted input, a credential worth
stealing, and a way to send data out. Shell and file tools (Bash, Read,
Write, Edit) are hard-denied for every agent, with two exceptions, and
neither one puts a credential next to the shell:

- **the platform-coder**, the agent that writes the platform's own pull
  requests behind the UI wizards, whose workspace is a throwaway clone with
  no secrets in it beyond the repository token it needs to push — and whose
  input is prose the API wrote, never a ticket or a chat message;
- **dev runs** — any agent with `role: dev`, such as the seeded engineer —
  which get a real shell on the [Workbench](workbench.md): a bigger pod on
  the `runner-dev` image, an *anonymous* clone of the public repository on a
  branch, the test toolchain, and **no git credential of any kind**. The pod
  cannot push. Its code leaves through one door, a publish the runner (not
  the model) makes to the API, which holds the GitHub App, checks every
  touched path against a deny list and the agent's grants, and pushes
  without force. What a dev pod holds is what every agent's pod holds — the
  run's own platform identity and session token, revoked when the run ends —
  so a prompt-injected dev agent can do what any agent can (speak as itself)
  plus propose code, as a PR a human reads.

(Unfamiliar component names — broker, executor, runner — are defined in the
[Glossary](glossary.md).)

## So how does an agent DO anything? MCP tools.

MCP (Model Context Protocol) is the standard way Claude Code calls tools that
live outside its own process, and it is the platform's answer here: instead of a shell, an agent gets a
menu of specific, named actions — `stocks`, `discord_chat`, `memory`,
`runs_read`, and so on. When the agent uses one, this happens:

1. **The call goes to the mcp-broker**, a small platform service that owns no
   credentials of its own. The agent's request carries proof of identity
   (below), never a password or API key.
2. **The broker checks who is calling** — it asks the platform API to
   verify the identity, then checks the agent's own definition (a database
   row an admin, or an agent holding `agents_grant`, controls) actually
   lists that tool. Not declared = not allowed, no exceptions.
3. **The tool-executor runs the tool's code** — code a human reviewed and
   merged through a pull request. The model only ever picks the
   *arguments* (which ticker, which channel, what text). It can never
   supply code.
4. **Secrets appear only at the last moment.** If a tool needs a
   credential (say, the Discord bot token), the executor fetches it from
   Kubernetes for that one call and hands it only to that tool's
   subprocess. It is never placed in any agent's environment, so there is
   nothing in the agent's world to steal.
5. **Everything is written down.** Every tool call lands in an audit
   trail — who called, on whose behalf, which tool, allowed or denied,
   how long it took. Arguments are stored as a fingerprint (hash), never
   raw. Rate limits stop a runaway or manipulated agent from hammering a
   tool.

## How the agent proves who it is

There is no password in the pod. Each run's pod gets two things:

- a **workload identity token** issued by Kubernetes itself — it says
  "this really is agent X's pod", rotates automatically, and works
  nowhere except this platform;
- a **run token** signed by the platform — it says "this is run #N, doing
  work for this person, with exactly these tools", and it only works when
  presented by that same pod.

Steal either one and it's useless: the first is worthless off the pod,
and the second is locked to the first. Even editing an agent's config
mid-run changes nothing — the run's permissions were frozen when it
started.

## The other guardrails, briefly

- **Network walls:** the tool-executor is the single door out for
  **tools** — a tool's code reaches a third-party API from there, with its
  secret injected for that one call, never from an agent's pod. Agent pods
  themselves *can* reach any host on port 443 (the egress rule is scoped to
  the port, not to hosts — the Workbench's anonymous clone and `npm ci`
  depend on it), and narrowing that is a known, deferred gap. What keeps it
  safe is that there is nothing in the pod worth sending: no Anthropic key,
  no tool secret, no git token. Services only accept traffic from the
  specific services that need them.
- **Anthropic key:** never in agent pods — a proxy injects it
  per-request, so agents literally have nothing to leak.
- **Everything is reviewed or logged:** skills, tools, and secret
  *declarations* live in git behind the [change loop](changes.md). Agent
  *definitions* are database rows instead — edits apply immediately, but
  every one is attributed and append-only in the agent's change log (see
  [Agents](agents.md)). Secret *values* live only in Kubernetes.

The full engineering version — threat model, the five-layer identity
roadmap, and what's live vs planned — is `docs/security.md` in the
repository.
