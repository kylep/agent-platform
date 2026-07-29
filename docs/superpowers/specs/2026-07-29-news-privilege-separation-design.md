# Privilege-separated news pipeline (breaking the lethal trifecta)

## Goal

Run the daily news job with **no single component holding all three** of: access
to secrets, exposure to untrusted content, and an exfiltration channel. Today the
`news` agent has all three (see the injection assessment). We split it so the
untrusted-input component has nothing to steal, and the credential-holding
component never sees untrusted input.

## Roles & data flow

```
morning-news Job (cron)
      │  fires
      ▼
┌─────────────────┐   structured JSON digest (run result)
│  GATHERER agent │ ─────────────────────────────┐
│  web tools only │                               │
│  NO creds/Bash  │        [Kafka: run.transcript / terminal result]
└─────────────────┘                               ▼
   sees untrusted web            ┌────────────────────────────┐
   content, but has              │  NEWS PROJECTOR (recorder)  │  trusted code
   nothing to steal              │  parse+validate JSON schema │  (not an LLM)
                                 │  dedup vs shared_news table │
                                 │  sanitize + format text     │
                                 │  record new URLs            │
                                 └────────────┬───────────────┘
                                              │ discord.channel.post {channel, text}
                                              ▼  [Kafka]
                                 ┌────────────────────────────┐
                                 │  CONNECTOR (poster)         │  holds bot token
                                 │  posts to #news as Pai      │  never sees raw web
                                 └────────────────────────────┘
```

## Why this breaks the trifecta

| Component | Untrusted input | Holds secrets | Exfil channel | Safe because |
|---|---|---|---|---|
| **Gatherer** | ✅ web + (later) #news | ❌ none | ✅ WebFetch URL | nothing to steal; can't reach env/files (no Bash/Read) |
| **Projector** | ⚠️ gatherer's JSON only | ✅ DB write | ❌ | trusted code, not an LLM — can't be "instructed"; validates+sanitizes |
| **Connector** | ❌ only projector text | ✅ bot token | ✅ Discord API | input is platform-generated sanitized text, no injection vector |

No box has all three ✅. A compromised gatherer can at worst emit a garbage
digest → the projector sanitizes it → at worst a weird `#news` post (content,
not credentials/shell). The bot token never enters a runner pod.

## Components to build

### 1. Runner — least-privilege unattended tools *(linchpin; CONFIRMED)*
Today a token-bearing agent runs `--permission-mode bypassPermissions` (allows
*everything*); a token-less agent runs default mode (headless → all approval-
gated tools denied). The gatherer needs to run **web tools only, unattended,
with no API token**.

Confirmed against the Claude Code CLI reference: `--allowedTools "<tool>…"`
(space-separated) marks exactly those tools to "execute without prompting for
permission" — headless, everything not listed is denied; and `--disallowedTools
"Bash" "Read" …` with a bare tool name "removes the matching tools from Claude's
context" entirely. So:

- Run credential-less agents (the gatherer) with **`--allowedTools` = their
  frontmatter `tools:`** (e.g. `--allowedTools "WebSearch" "WebFetch"`) and
  **`--disallowedTools "Bash" "Read" "Edit" "Write" "NotebookEdit"`** for
  defense-in-depth — no token, no `bypassPermissions`. The gatherer can search
  and fetch, and literally cannot Bash or read files.
- This generalizes into a platform hardening: prefer scoped `--allowedTools`
  over blanket `bypassPermissions` for every agent (a follow-up, not required
  for the news split).

### 2. `shared_news` dedup table + news projector
- New table `shared_news {url PK, title, section, posted_at}` (server-owned; no
  agent token touches it).
- Projector lives in the **recorder** (which already projects
  `conversation.outbound` from terminal run results). On a terminal run of the
  gatherer agent: parse its result as the digest JSON schema; **validate**
  (reject/flag malformed); **filter** items whose URL is already in
  `shared_news`; **sanitize** the text (strip `@everyone`/`@here`/role mentions,
  cap length, enforce schema — the digest is the *only* content posted);
  publish `discord.channel.post`; insert the new URLs; prune >14d.
- **All dedup is server-side** — the gatherer needs no memory access at all. (It
  may gather already-shared stories; the projector drops them before posting.)

### 3. Connector — channel post
Extend `connector-discord` to consume a new `discord.channel.post` event
`{channel: "news", text}` and post to that channel via the bot API (resolve
`#news` by name). The connector is the single place the bot token lives.

### 4. Gatherer agent + output contract
Rework the current `news` agent into `news-gatherer`: `tools: WebSearch,
WebFetch` only; **no** `memory:true`, **no** `skills`, **no** bound secrets.
Its `agent.md` = the curation rubric/watchlist (ported from multi) + a strict
instruction to emit ONLY this JSON as its final message:
```json
{"date":"YYYY-MM-DD","items":[{"section":"AI industry","headline":"…","why":"…","url":"https://…"}],"note":"optional"}
```
Retire the `discord-bot` skill from the gatherer (posting moved to the
connector). Point the `morning-news` Job at `news-gatherer`.

## Residual risks (documented, out of scope here)

- **Shared Claude token** is still mounted in every runner pod. The gatherer
  can't reach it (no Bash/Read), but fully brokering it is a separate
  platform-wide hardening (mitigation #4).
- **Bad-post containment:** a poisoned digest can still yield an odd `#news`
  post. Sanitization bounds it to text. *Optional extra:* post to a staging
  channel or require a 👍 before publishing — add if desired.
- **Gatherer quality:** the JSON-only constraint may slightly reduce curation
  polish vs a free-form agent; acceptable.

## Testing

- Runner: token-less agent runs with `--allowedTools` = declared tools; Bash is
  denied; web tools run.
- Projector: valid digest → filtered + `discord.channel.post` emitted + rows
  recorded; malformed/injected digest → sanitized/rejected, no mentions leak;
  dedup drops known URLs; prune drops >14d.
- Connector: `discord.channel.post` → posts to the resolved channel.
- End-to-end: Run Now the job → gatherer emits JSON → projector filters →
  connector posts to `#news`; second run dedups.

## Effort (rough)

Runner change S–M · shared_news + projector M · connector channel-post S ·
gatherer rework + schema S–M · tests throughout. One focused build.
