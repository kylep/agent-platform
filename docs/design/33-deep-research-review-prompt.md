# Independent deep-research review prompt

Paste this prompt into OpenAI, Gemini, or Claude Deep Research, followed by the full contents of `33-capabilities-plugins-and-live-apps.md`. Ask each model to work independently; do not give it the other models' answers first.

---

You are an independent principal engineer reviewing a proposed redesign of a self-hosted agent platform. The design document follows this prompt. Research current **primary sources** (official MCP specification/extensions, Claude Code and OpenAI Codex documentation, relevant official security standards/docs) and challenge both factual claims and architecture. Treat the document as a proposal, not authority.

Context and constraints: the platform already runs on one Kubernetes NUC with Postgres, Kafka, an API, web UI, MCP broker/facade, isolated tool executor, artifacts, Relay, and Claude/Codex agent runners. No new infrastructure service is desired. App definitions and Live View content should be database state; domain algorithms can remain reviewed code. Apps are named collections of pages/actions. Live Views must be able to call general Tools, including effectful ones, through a constrained host bridge. Chat Identities may hold several secret references. One reviewed `agent-platform-coding` skill package should work for platform agents and be distributable to local Claude Code and Codex. Existing agent/tool grants, workload identities, old Apps and data must survive migration. Favor a useful vertical slice and a reversible rollout over a grand rewrite. Do not assume any host supports MCP Apps unless official docs or a reproducible test establish it for the named version.

Deliver a **decision review**, not a summary. In this order:

1. A verdict on the core model: what to keep, change, or reject. Identify at most five architectural decisions that determine whether this becomes a proud, durable platform rather than an impressive demo.
2. A fact-check table for MCP Tools/Resources/Prompts/MCP Apps, Claude Code plugins/marketplaces, Codex plugins/marketplaces, and browser security claims. For each material claim, mark verified/conditional/incorrect and link the **exact official source** that supports the finding. Distinguish current stable specifications from drafts and host-specific behavior.
3. A threat model for agent-authored HTML/JS calling general Tools: viewer versus author authority; human Tool grants; adapter credentials; Chat Identity impersonation; hidden/automated writes; prompt injection; forged `postMessage`; XSS/sandbox escape; CSRF; result-data leakage; provider cost; retries and unknown outcomes. Give at least one concrete exploit path against a weak implementation and show how the proposed boundary blocks it. Do not hand-wave with “sandbox it.”
4. Challenge the App migration. For Running, News, Stockmarket, TTRPG, and TCMS, distinguish UI from ingestion/projection/domain behavior. Identify where DB pages provide value and where code must stay. Propose the smallest full pilot that proves general Tool calls, not only chart rendering.
5. Challenge plugin distribution across the **pinned runner versions**, not just current laptop versions: Claude Code 2.1.214 and Codex 0.155.1. Address skill selection, namespace/collisions, launch latency, offline reproducibility, immutable bundle retention, secret grants, local marketplace installation and rollback. Mark anything needing a real binary smoke test.
6. Review the phase/task plan for missing dependencies, cost traps, irreversibility and low-value complexity. Suggest concrete edits to the design, each with a section reference, priority and acceptance test. Include a “cut list” if weekly model quota or engineering attention becomes tight.
7. State what evidence would make you change your mind. List unresolved questions that truly require the product owner, separated from implementation choices an engineering team can make.

Keep the response specific to the supplied design and system. Cite URLs next to claims, identify inferences, and avoid generic best-practice lists. Do not propose an entirely new database, queue, object store, or cluster service unless you demonstrate why the existing infrastructure cannot meet a requirement. Do not implement code or modify the repository. If you cannot access the repo, say which claims require a local audit rather than inventing current implementation details.

---

Paste the design document below this line.
