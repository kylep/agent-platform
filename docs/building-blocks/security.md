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
Write, Edit) are hard-denied for every agent except the platform-coder,
whose workspace is a throwaway clone with no secrets in it.

## So how does an agent DO anything? MCP tools.

MCP tools are the platform's answer: instead of a shell, an agent gets a
menu of specific, named actions — `stocks`, `discord_chat`, `memory`,
`runs_read`, and so on. When the agent uses one, this happens:

1. **The call goes to the MCP broker**, a small service that owns no
   credentials of its own. The agent's request carries proof of identity
   (below), never a password or API key.
2. **The broker checks who is calling** — it asks the platform API to
   verify the identity, then checks the agent's own definition in git
   actually lists that tool. Not declared = not allowed, no exceptions.
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

- **Network walls:** agent pods can't reach the internet at all; the
  tool-executor is the single door out. Services only accept traffic
  from the specific services that need them.
- **Anthropic key:** never in agent pods — a proxy injects it
  per-request, so agents literally have nothing to leak.
- **Everything is reviewed:** agents, skills, tools, and secret
  *declarations* live in git behind the change loop. Secret *values*
  live only in Kubernetes.

The full engineering version — threat model, the five-layer identity
roadmap, and what's live vs planned — is `docs/security.md` in the repo.
