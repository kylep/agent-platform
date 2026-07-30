# Design note — brokering the shared Claude token out of runner pods

Status: **partially done (enforcement hardened); full removal deferred — needs a
decision.** Tracks the long-standing residual from
[08](08-news-and-injection-hardening.md): the subscription Claude token
(`claude-credentials` → `/secrets/claude/token`) is mounted read-only in every
runner pod, because the agent process *is* `claude` and needs
`CLAUDE_CODE_OAUTH_TOKEN` to authenticate to Anthropic.

## The actual risk

A token is only dangerous if an agent can both **read** it and **exfiltrate** it.

- **Read** needs a token-reading tool — `Bash`/`Read` (the token is a file and an
  env var). Web/MCP tools can't reach it.
- **Exfiltrate** needs egress to an attacker host. Runner egress allows outbound
  443 to *any* host **by necessity**: `news`/`pai` use WebSearch/WebFetch and
  must reach arbitrary sites. So egress can't be allow-listed for web agents —
  that path is inherent.

The platform's safety therefore rests on the **trifecta break**: no single agent
has (untrusted input) + (token read) + (open egress). Audited 2026-07-30:

| agent | tools | input | token-read? |
|---|---|---|---|
| news / pai | WebSearch, WebFetch | untrusted (web/Discord) | no |
| health-monitor / run-summarizer | `mcp__platform__*` | internal | no |
| platform-coder | Read, Write, Edit, Bash | **trusted** (Kyle's edits) | yes, but trusted input, ephemeral clone |

Only the self-edit agent can read the token, and its input is trusted. The break
holds.

## What shipped (2026-07-30): make the break enforced, not conventional

Previously the runner only stripped a sensitive tool from a non-self-edit agent
when the manifest *didn't declare it* — so a manifest that declared `Bash` on a
web agent (a mistake, or a prompt-injected self-edit editing `agents/`) would
re-arm the trifecta. `runner._permission_args` now **always** denies the
token-reading tools (`Bash`/`Read`/`Edit`/`Write`/`NotebookEdit`) for every
non-self-edit run, even if declared. Token-read tools are self-edit-only by
construction; no tool list can grant a web agent the token. Zero current agents
are affected (none declare those tools).

## What's deferred: removing the token from the pod (needs a decision)

The residual — the token is *present* in pods that also have open egress — is
only fully closed by keeping it out of those pods. The only viable design is an
**auth-injecting egress proxy**: a sidecar (or per-node service) that holds the
token, and `claude` runs with **no token**, pointed at the proxy via
`ANTHROPIC_BASE_URL`; the proxy adds `Authorization` and forwards to Anthropic.
The token then lives only in a container the agent can't exec into.

**Why it's deferred rather than done autonomously:**

1. **Open feasibility question.** It's unverified whether `claude`'s
   *subscription OAuth* works when pointed at a custom base URL with no token in
   the process — the client may refuse to start without a credential, or the
   OAuth flow may not tolerate a proxy. This needs a spike with the real token,
   which shouldn't be done casually on prod.
2. **Blast radius.** Getting the proxy or `ANTHROPIC_BASE_URL` wiring wrong
   breaks **every agent run** (nothing can reach Anthropic). This is not a
   change to land unattended.
3. **It closes defense-in-depth, not an active hole.** The trifecta break holds
   and is now enforced, so the token is not currently reachable by any
   untrusted agent.

**Recommendation:** treat full token-brokering as a scheduled spike — first
answer the feasibility question (does `claude` + subscription OAuth work through
an auth-injecting proxy?), in a throwaway environment, before committing to the
architecture. Until then the enforced trifecta break is the guarantee.
