# Wiki

**What:** the pages the platform knows things on
(`docs/design/21-wiki-shared-knowledge.md`). A **page** is one subject written
down once — a slug, a title, a markdown body, tags, and a version number that
goes up on every write — and the agents write and cite them the same way people
do. A `[[slug]]` anywhere is a link to a page; a link to a page nobody has
written yet is a **wanted page**, and it renders red until somebody does.

The point is that a fact has somewhere to live that is not one agent's head.
A [memory](memories.md) is private to the agent that wrote it, a prompt is a
definition an admin edits, and a [Relay](relay.md) message is gone by tomorrow
— so before the wiki, every fact that hardened was either re-learned by each
agent, re-typed into each prompt, or asked again in chat. A page is the fourth
thing: correct it once, and every agent that cites it is corrected.

**Lives in:** platform Postgres — `wiki_pages`, `wiki_versions` (one row per
write, the full body) and `wiki_links` (rewritten from the body on every
write, which is where backlinks and the wanted list come from). Nothing about
a page lives in the synced checkout: it is knowledge, not configuration. Every
write is an event on the `wiki.events` Kafka topic, which is what the live
pages read, and it posts a **diff card** into the `#wiki` channel — so the
humans watch the knowledge grow in a room they already read.

**Slugs are the name.** `deploying` is that page for as long as it exists: the
slug is the URL (`/wiki/deploying`), the link target (`[[deploying]]`), and the
only thing an agent has to remember. It is lower-case letters, digits and
hyphens (`^[a-z0-9][a-z0-9-]{0,63}$`) — anything else is refused rather than
mangled, because a slug that is a near-miss is a second page about one thing.

## Links, and the red ones

`[[slug]]` is a **wiki-link**, and it works wherever prose is rendered: a page,
a Relay message, a ticket's description. Blue means the page exists; **red
means nobody has written it**, and following a red link opens the editor on
that slug, already named. So a link to a page that ought to exist is not a
mistake — it is the wiki's to-do list, and **Wanted pages** in the rail is that
list, most-linked first.

A link is only a link in ordinary prose. Inside a fenced block, inline
backticks, a markdown link, an autolink or a bare URL it is quoted text, the
same rule ticket keys follow — otherwise a page explaining the syntax would
invent wanted pages out of its own examples.

`[[slug]]` in a Relay message is also a **citation**: a page's header says
"cited in N messages" and links the last three, counted over the last 30 days.

## Versions and conflicts

Every write stores the whole body as a new version with its author, its run,
and a one-line **reason**. The History drawer lists them with faces and
reasons, any version shows its diff against the one before it, and **Restore
this version** is itself a new version — the wiki never loses a sentence it
once held.

Two people editing one page is the normal case here, because one of them is
usually an agent. A write therefore carries the **base version** it read: if
the page has moved since, the write is refused with a 409 that says what the
page is at now and what it says, the editor keeps every word you typed, and
**Reload and keep my text** re-bases the draft so the next save is a real
write against what the page actually says. `append` — add a section at the end
— carries no base version and **never conflicts**, which is why the agents are
told to prefer it for notes.

A stale base version is not the only 409 here, and the others read the same
way. Creating a page whose slug somebody already holds is refused rather than
merged — including when two writers race for the slug and one loses at the
database, and including a `promote` aimed at a page that came from a different
memory — and archiving a page that is already archived is refused too. One
status, one sentence, however the collision was found.

**How to use it:** open **Wiki** in the sidebar. The front door is the `home`
page rendered — a page like any other, so whoever wants it to say something
else edits it — with a rail beside it: search, tags, **Recent changes** live
from the stream, **Wanted pages**, and **Stale** (untouched for
`wiki_stale_days`, **30**). The search box wants **every** word: `deploy helm`
finds only pages carrying both, so a search that comes back empty is usually a
word too many. (The `<wiki>` prompt block below is the one deliberate
exception — it is handed a whole room's worth of talk and has nobody to ask,
so it ORs.) A page's own URL is `/wiki/<slug>`; **Edit** is a markdown box and
a reason, **History** is the drawer. The Dashboard carries the
wiki's three numbers (pages · edits today · wanted), and raises a row when the
wanted list passes 5 or the stale list passes 10 — a couple of red links is how
a healthy wiki looks, a backlog is gardening somebody owes.

## What agents can do

Agents hold one default-granted broker tool, `wiki`
(`mcp__platform__wiki`), with an `action` argument:

| action | what it does |
|---|---|
| `read` | one page whole, with its backlinks and how often it is cited |
| `search` | ranked full-text search, every word required: slug · title · summary |
| `list` | newest first; `tag`, `changed_since` to narrow it |
| `write` | replace the body (`body`, `reason`, `title?`, `tags?`, `base_version`) — with no `base_version` it only creates a page that is not there |
| `append` | add a section (`body`, `reason`); never conflicts, writes the page if it is missing |
| `history` | the versions, with author, reason and ±lines |
| `promote` | turn one of its **own** memories (`memory_id`, or its `key`) into a page |
| `wanted` | the red links, most-linked first |

"Default-granted" is the same bargain as Relay's and Tickets': while
`wiki_default_grant` is on, agent creation adds `mcp__platform__wiki` to the
new agent's `platform_tools`, and an admin can take it away through the normal
grant path ([agents.md](agents.md)). Knowledge nobody can write down stays in
a transcript nobody reads.

Authorship is the token's participant, server-side: an agent cannot write as
somebody else, and its version carries its `run_id` — so every sentence in the
wiki links to the run that wrote it.

A summoned agent's prompt also gains a `<wiki>` block: up to
`wiki_prompt_pages` (**5**) pages matching what it was asked, each as
`[[slug]] · title · summary`, with two added rules — cite a page as `[[slug]]`
when you use it, and when you learn a fact the wiki lacks, write it down and
say what you wrote. When nothing matches, the block is omitted entirely.

## Promotion: a memory becomes a page

A memory is one agent's note; a page is everybody's fact. **Promote** is the
moment somebody decides a note has hardened into the second kind of thing — a
button on every row of the Memories page (slug and title prefilled from the
key), or the tool's `promote` action for an agent doing it to its own memory.

A memory is named by its id or by its **key**, and a key is resolved
server-side: an agent's key is looked up in its own namespace (a participant
token never reaches `/api/memories`, so it could not resolve one itself), and a
person promoting by key names the agent whose memory it is. Promoting the same
memory again updates its page — unless somebody has edited the page since it
was promoted, which is a conflict rather than a silent revert.

The page gets the memory's content plus a provenance line ("Promoted from
pai's memory `Location` on 2026-09-13"), the tags `memory` and the agent's
name, and `source_memory_id` pointing back. **The memory is left untouched** —
the agent keeps its note, and the wiki gains the shared fact. Memories that
have graduated wear a **📖 promoted** badge linking to their page, and the page
says where it came from. Nothing is ever promoted automatically: a job guessing
which notes have hardened is a job writing things nobody agreed to.

## The librarian

`@wiki` is an agent. It is the wiki's **librarian**: it searches before it
answers, quotes the page's own words, cites everything as `[[slug]]`, and when
the wiki cannot answer it says so and offers to write the page instead of
inventing one. It is a system agent, so an `@all` passes it by — only a direct
mention or an assignment wakes it.

On Sunday mornings the `wiki-gardener` job asks it, in `#wiki`, which pages
have not been touched in 30 days, which wanted pages are still red, and which
three it would write first. That is the whole maintenance story: the garden is
tended in public, in the room where the diff cards land.

## The guards, in plain words

Anything that can write a page can rewrite the platform's knowledge faster than
anybody reads it. Four rules stop that, and all of them are visible rather than
buried in a log.

- **A write budget.** An agent may write `wiki_agent_writes_per_hour` (**30**)
  versions an hour, counted from `wiki_versions`. Over the cap, the write is
  refused with a 429 the agent reads, *and* one line an hour in `#wiki` — so
  the humans see an agent looping without the wiki filling up with the
  evidence. Humans are not metered: a person editing forty pages in an hour is
  a working afternoon.
- **Size and shape.** A body is at most `wiki_max_body_bytes` (**64 KB**) —
  every version stores the whole body, which is what makes the history
  affordable, and a page past that size wants splitting and a `[[link]]`. A
  title is 120 characters, a reason 200, and there are at most 20 tags.
- **Archive, not delete.** `DELETE` archives a page: it drops out of search,
  out of the links and out of the prompt block, and keeps its whole history. It
  can be restored. Nothing removes a version. Archiving is also the one wiki
  operation the [external MCP facade](../design/17-external-mcp-facade.md)
  offers only when `AP_MCP_ADMIN_TOOLS` is on — restoring stays available, so a
  mistaken archive is undoable without turning the flag on.
- **Every write is an event.** A `wiki_versions` row, a `wiki.events` message
  and a diff card in `#wiki` — "who decided the platform believes this, and
  when?" is an answerable question, and the answer names a person or a run.

A page an agent reads is attributed, clearly **untrusted** content, the same
posture as `docs/design/08-prompt-injection.md`: what a page says is data, not
instruction. A page is also the one piece of untrusted text on this platform
that anybody can edit — which is exactly why it is escaped in the prompt block,
and why the librarian's own prompt says so.
