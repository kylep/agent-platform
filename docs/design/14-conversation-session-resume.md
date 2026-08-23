# 14 — Conversation session resume

## Problem

Conversation continuations are stateless full-transcript replays: each turn is a
fresh `claude -p` whose prompt is the entire history flattened into one string
(`conversation.py:build_prompt`). Measured on a live conversation (2026-08-23):
`cache_read_input_tokens` stayed flat at 2277 across turns — only the system
prompt + tool definitions hit Anthropic's prompt cache. The flattened history is
a single user block whose bytes change every turn (and the trailing "Respond to
the latest user message" instruction sits after it), so the transcript re-bills
at full input price on every turn. The flattening also throws away tool_use,
tool_result, and thinking blocks — an agent cannot remember what tools it ran
last turn. History was additionally capped at an arbitrary 20 turns.

## Rejected: structured message replay via --input-format stream-json

Tested on CLI 2.1.241: injected `{"type":"assistant",...}` stdin lines are
silently DROPPED — each user line becomes a live turn the model answers itself.
There is no CLI/SDK path to seed assistant history into a fresh process.

## Decision: persist the CLI session file as an opaque blob

`claude --resume <sid>` restores everything from
`~/.claude/projects/<cwd-slug>/<sid>.jsonl` (full message history incl.
tool_use/thinking). Verified: the file IS the state — delete it and resume
fails; restore the exact bytes and resume works, with the replayed history
hitting the prefix cache (measured `cache_read: 29482` on the resumed turn).

Flow per conversation turn:
1. Backend stores `(claude_session_id, session_blob)` on the Conversation row.
2. Runner (run pod) GETs the blob via a run-scoped `session` token, writes it to
   the CLI's expected path, and invokes `claude --agent X --resume <sid> -p
   "<new user message>"`.
3. Runner captures the turn's `session_id` from the result frame and PUTs the
   updated `.jsonl` back.
4. Fallback: no blob / restore failure / resume exit != 0 -> the existing
   flattened text replay (now token-budgeted instead of 20-turn-capped). A
   fallback turn starts a fresh session whose blob replaces the old one —
   self-healing.

The blob is opaque: we never parse or generate the .jsonl (its format is
internal to Claude Code and version-dependent). An oversized PUT clears the
blob instead of keeping a stale one — a stale blob would resume a session
missing recent turns, which is worse than a clean reset.

## What this buys

- Fidelity: tool calls, tool results, and thinking survive across turns.
- Cost: the replayed history becomes a byte-stable prefix -> cache reads at ~10%
  of input price (1h TTL on the subscription tier; conversations idle >1h pay
  one re-write, then hit again).
- The platform still owns history: (user_message, result) pairs on Run rows
  remain the source of truth for the UI, connectors, and the fallback path.

## Cache metrics

The recorder previously dropped `cache_read_input_tokens` /
`cache_creation_input_tokens` (and the per-model equivalents in `modelUsage`).
They are now captured on Run and RunModelUsage and surfaced in
/api/metrics/* and the Reporting page, so cache health is observable instead of
a one-off spot check.

## Known limits

- Resume replays history to the API every turn (no server-side session state) —
  the win is cache pricing + fidelity, not fewer tokens sent.
- A CLI version bump in the runner image may invalidate old session files; the
  fallback path absorbs this (turn still succeeds, new session starts).
- Session growth: blobs beyond `session_blob_max_bytes` reset to fallback,
  which also bounds context growth.
