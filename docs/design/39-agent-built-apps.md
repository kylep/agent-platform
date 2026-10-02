# 39 — Agent-built Apps

Status: **designed 2026-10-02**, revision 3, after two review passes by each
of Fable, Sol, Sonnet and Astra. Awaiting Kyle's decisions on the open
questions at the end. Kyle's framing: Apps are for agents. Agents build and
maintain them, from simple reports to full tools; they can ask humans for new
App Builder tools, but the agents themselves build the Apps. The build plan
will be `docs/superpowers/plans/<date>-agent-built-apps.md`.

## The problem

Design 33 made an App's definition state: collections, live pages and actions
are database rows an admin publishes without a deploy. That won the
presentation layer. Three things still keep agents from building Apps:

1. **Agents can't author anything.** Every live-view authoring route is
   `require_admin` (`api/live_views.py:493-563`), and `_reader`
   (`live_views.py:205-210`) rejects any agent-bound key ("Live Apps require a
   human principal"). No platform tool lets an agent create an App or a page.
2. **There is nowhere to put data.** A live page can read only ten hardcoded
   bindings (`READ_FIELDS` / `TABLE_FIELDS`), and a page in a new App can bind
   only Relay and wiki reads (`_check_app_bindings`, `live_views.py:186`).
   Only two actions exist (`tickets.create@1`, `relay.channel.post@1`). An App
   that owns records needs a coded domain service.
3. **Nothing teaches them.** Even with tools, an agent needs to know when an
   App is the answer, how to model data, what needs Kyle's approval, and how
   to keep what it built working across many runs.

The judgment App (design 38) is the measuring stick: one persona request, a
design, three implementer agents, two review rounds, a platform auth change
and about 3,000 lines of code.

## What this delivers

- **Builders:** an `apps` tool with which agents create Apps they own and
  draft, validate, preview, publish, propose, roll back and retire their
  definitions. Every operation is versioned, attributed and resumable.
- **Collections:** state-defined data with typed fields, write modes,
  field-level access, references and rules. One generic `app_data` tool reads
  and writes records for every App, so no App needs its own code.
- **Views:** declared queries that pages and agents share.
- **`typed/v2` pages:** tables, details, forms, metrics, charts and buttons
  bound to views, under design 33's typed-render rules (no HTML, no
  JavaScript).
- **An authority model Kyle controls:** agents change what they build freely
  until a change would widen what anyone can see or do. Widening is a frozen
  proposal Kyle approves from his own session.
- **A request path** for missing primitives, and an **`app-building` skill**
  for Pai, Kai and Olu.

Not delivered: author HTML/JavaScript, Apps visible to humans other than
Kyle, cross-App reads, or an expression language.

## Starting point (review)

### Live pages (design 33, as built)
- **Tables:** `app_collections` (navigation identity; `owner_id` is a human
  principal, default `admin`), `live_views` / `live_view_versions` (draft
  JSON, immutable published versions), `live_operation_grants`,
  `live_intents`, `live_invocations` (receipts), `live_snapshots`.
- **`typed/v1`:** up to 50 blocks of `heading | paragraph | metric | table |
  chat | action | link`, 10 reads and 10 actions. It is validated against
  `READ_FIELDS` / `TABLE_FIELDS` and `operation_catalog.admitted()`, and the
  renderer is a `Literal["typed/v1"]`.
- **Rendering:** a trusted React switch in
  `services/web/src/pages/LiveView.tsx`. A page whose published definition no
  longer validates returns 503 at read time (`live_views.py:402`).
- **Actions:** intent → call → receipt. They need a browser session and a
  per-principal grant, run as the clicking human, and are re-checked at
  dispatch. The flow is hardwired to the two operations
  (`live_invocations.py:81, 163`) and their argument types and digest
  (`:143`).
- **Preview:** client-side, with sample data only (`LiveViewEditor.tsx`).

### Agents' authority today
- Run tokens freeze an agent's tools at launch (`_caller_platform_tools`,
  `agents.py:200`), and `authorization_generation` fences old runs after a
  grant change.
- **`agents_grant` can grant any grant field to any agent, including the
  caller** (`agents.py:106, 172-234`).
- **`agents_edit` can rewrite another agent's prompt.**
- **`agent_self` edits the caller's own non-grant fields.**

### Skills
Skills are reviewed `SKILL.md` files in a pinned, CI-attested plugin release
(`plugin_release.py`: `APPROVED_RELEASES`, `ATTESTED_BUNDLES`). Only
`SKILL.md` is admitted, up to 64 KiB, and every change costs a release cycle.

### Events
Crons, webhooks, Jobs and one-time Tasks (design 37) start runs. Relay
mentions and ticket assignment summon agents. `entrypoints.topics` is stored,
but nothing consumes it.

## Principles

1. **Construction is not authority.** Agents build; Kyle decides who may see
   or change what. A change that widens anyone's access or authority takes
   effect only when Kyle approves its exact content.
2. **Typed, not scripted.** Every App is data the platform interprets.
   Nothing an agent writes ever executes.
3. **An App is consistent or unpublished.** A publish validates the whole App,
   including its existing records; nothing that publishes leaves a page or
   view broken.
4. **Data outlives definitions.** Records persist across schema versions, and
   nothing an agent publishes silently drops data.
5. **Builds are resumable.** An agent that loses its run mid-build can find
   exactly where it stopped.
6. **Code is the escape hatch, not the norm.** Domain services stay right for
   genuine computation or external I/O. The request path turns repeated needs
   into primitives.

## Vocabulary

- **App:** an immutable id, a name that is never reused, an owner, versioned
  definitions (collections, views, pages, action templates) and build notes.
- **Collection:** a typed record set inside one App.
- **View:** a declared query over one collection.
- **Page:** a `typed/v2` live view bound to views and action templates.
- **Action template:** a named, closed-form write that a page offers Kyle.
- **Bundle:** draft definitions published or proposed together.
- **Approved state:** the App's published definitions. Everything in it was
  self-published within the rules or approved by Kyle.
- **`kyle`:** the platform's owner principals (setting `KYLE_PRINCIPALS`,
  default `admin`), and only when authenticated by browser session
  (`X-AP-Auth: session`, design 38). It is a stored identity, never a name
  match.
- **Builder:** an agent holding the `apps` tool.
- **App tool:** the tool category for `apps`, `app_data`, `tcms`, `ttrpg`,
  `backtest` and `judgment`.

## The authority model

### Effective authority facts
The server computes an App's **effective authority** as a closed set of fact
tuples, each derived from the definitions:

| fact | tuple |
|---|---|
| field access | `(principal, collection, field, verb)`, verb ∈ `read`, `create`, `update` |
| record delete | `(principal, collection, delete)` |
| delete reach | `(collection → collection, on_delete)` edges, as a closure |
| rules | each rule in canonical form, keyed by kind and target |
| action templates | `(template, kind, collection, presets, editable_fields)` |
| outbound links | `(collection, field)` that renders as an external link |
| triggers (Release 3) | `(collection, event, summoned agent)` |

Principals are `owner`, `kyle` and named agents. Quotas are platform-owned:
Kyle sets them and builders can't, so they aren't builder authority.

### Self-publish vs proposal
A bundle **self-publishes** when it is consistent and every fact in its
effective authority is present in the approved state's, or narrower. The test
is exhaustive over the tuple schema; a definition field the engine can't map
to a tuple makes the bundle a proposal ("unknown means proposal").

- **New fields don't widen.** A new field's readers and writers default to
  `owner` and `kyle` only, even in a collection shared more widely. Giving a
  new field to existing readers is a proposal. So "add a field" self-publishes
  without exposing anything new.
- **Adding a rule self-publishes.** Every rule restricts.
- **Relaxing a rule is a proposal.** Per kind:
  - a `writer` rule's writer set grows, or its `value` qualifier is added,
    changed or removed;
  - `immutable_after_create` loses fields;
  - `unique` or `required_when` is dropped or loses fields;
  - a `lock`'s locked fields shrink, its `when` changes, `in: history`
    becomes `current`, or `unless_writer` grows.

  Any other change to an existing rule counts as a removal plus an addition,
  so it's a proposal unless the engine proves the new rule strictly narrower.
- **Always a proposal:**
  - a new action template, or any change to one's target, presets or
    editable fields (a template asks Kyle to act);
  - an outbound link;
  - a trigger;
  - a wider delete reach;
  - a change that drops data;
  - a rollback to anything wider than the approved state;
  - an ownership transfer.
- **Granting a template brings its access with it.** A template that writes
  a collection includes, in the same proposal, the `kyle` `create` / `update`
  facts it needs, so approving the form makes it work.

Maintenance of an App Kyle already approved as shared stays self-publishable
as long as nothing widens. A new App starts with the narrowest approved
state: owner and Kyle read, the owner writes, no templates.

### Proposals
- **Freeze.** `apps propose` freezes the bundle as a content-addressed
  snapshot (digest over canonical JSON), with the approved-state version it
  was computed against and the platform-generated authority delta. The delta
  is the changed fact tuples in plain words, plus a validation summary.
- **Review.** Kyle approves on the App's review page. The route requires
  Kyle's session and the digest he was shown.
- **Approve atomically.** In one transaction, approval re-runs consistency
  checks against current records, re-computes the delta, and publishes with a
  compare-and-swap on the base version. If the App moved or the delta
  changed, the proposal becomes `stale`, and nothing publishes.
- **Notify.** A Relay card in the owner's home channel, assigned to Kyle,
  links to the review page. The card never approves anything.
- **States:** `open`, `published`, `declined`, `stale` or `withdrawn`. The
  builder reads status and can withdraw a proposal.
- **Sharing doesn't grant tools.** A proposal that shares an App with another
  agent only adds that agent's facts. The agent also needs the `app_data` tool,
  a separate, Kyle-only grant. Running runs see neither, because their tokens
  are frozen.

### Phase 0: closing escalation first
- **Kyle-only tools.** `KYLE_ONLY_TOOLS` (`apps`, `app_data`, `agents_grant`,
  `agents_edit`) can be granted or removed **only by Kyle's session**, on any
  target agent, on both create and update (`agents.py` `_changed_fields` /
  `authorize`, and the create path at `:612`). This closes self-grant,
  proxy-grant and mutual-grant for these four tools. It doesn't touch other
  grant fields (`secrets`, `role`, `can_invoke`, `push_path_globs`), which
  remain a separate, known gap.
- **Protected agents.** An agent holding any `KYLE_ONLY_TOOLS` entry can be
  edited (prompt included) only by Kyle's session or by `agent_self`. This
  stops anyone with `agents_edit` from steering a builder.
- **No self-edits** through `agents_grant` or `agents_edit`.
- **Audit.** A migration records every current holder of a Kyle-only tool;
  today that's Kai. Its access is kept until Kyle decides (open decision 2).
- **Tests and docs.** A security test matrix: self, proxy, mutual,
  create-with-grants, and editing a protected agent. The broker's "grant what
  you don't hold" text is rewritten.
- **Agent paths stay narrow.** Agents reach Apps only through the `apps` and
  `app_data` tools, which use new broker-backed API routes checked against
  the run token's frozen tools and App ownership. Browser live-view routes
  stay human-only.
- **Schema.** `app_collections` gains `owner_agent`, an immutable `id`, and
  retired-name reservation. When an owner agent is deleted, its Apps transfer
  to Kyle.

## Collections

The `apps schema` action returns the full JSON Schema for every definition
kind, so builders and validators share one source. An example:

```json
{
  "collection": "habits",
  "fields": {
    "habit": {"type": "enum", "values": ["run", "read", "stretch"], "required": true},
    "day":   {"type": "date", "required": true},
    "done":  {"type": "bool", "required": true},
    "note":  {"type": "string", "max": 500, "access": {"read": ["owner"]}}
  },
  "write_mode": "editable",
  "access": {"read": ["owner", "kyle"], "create": ["owner"], "update": ["owner"],
             "delete": ["kyle"]},
  "rules": [{"kind": "unique", "fields": ["habit", "day"]}]
}
```

- **Field types** (Release 1): `string` (≤ 1,000), `text` (≤ 16,000), `int`,
  `number`, `bool`, `date`, `datetime`, `enum`, `ref` (same App), and `url`.
  A `url` renders as plain text unless the field sets `link: true`, which is
  an outbound-link fact. Release 2 adds `list` of a scalar (≤ 50 items) and
  `ref` with `pin_version`. A general `message_ref` (Relay/Discord message
  references) arrives via the request path if Apps need it. There is no
  free-form JSON field.
- **System fields** (read-only, available to views and rules): `id`,
  `created_at`, `updated_at`, `author`, `version` and `collection_version`.
  `author` is stamped from the run token or Kyle's session.
- **Write modes:** `editable` (update in place with `expected_version`) and
  `immutable` (write once) in Release 1; `versioned` (append a version per
  update) in Release 2.
- **Access:** a collection-level `access` sets defaults per verb, and a field
  may override with its own `access` (narrower or wider). Every path checks
  access per field, for the actual caller, at execution:
  - query results;
  - filter, sort, group and aggregate on a field the caller can't read is
    refused, which closes inference side channels; this includes `exists`
    sub-filters;
  - `expand`, which returns restricted fields as `null` with a `restricted`
    marker;
  - record history;
  - previews.
- **References** stay inside one App, with `on_delete`:
  - `restrict` refuses the delete while referenced;
  - `unlink` clears the reference;
  - `cascade` deletes the referencing records (Release 2).

  Deletes run a server-computed plan in one transaction.

### Rules
A closed set, and every rule is a guard:
- **`writer`** `{field, value?, writers}`: only these writers may set this
  field, or this value of it.
- **`immutable_after_create`** `{fields}`.
- **`unique`** `{fields}`, enforced under a per-collection advisory lock
  (`pg_advisory_xact_lock` keyed on App and collection).
- **`required_when`** `{field, when: {field: value}}` (Release 3).
- **`lock`** `{when: {field, value, in: current|history}, lock: [fields],
  unless_writer}` (Release 3).

A rule added over existing records is checked against them first, under the
same advisory lock. The publish lists the violating record ids and refuses
until the bundle fixes them or narrows the rule.

### Schema evolution
- **Additive** changes publish under the normal rules: optional fields, wider
  enums and limits, rules that existing records satisfy.
- **Destructive** changes before Release 3 mean "new collection, copy, retire
  the old one".
- **Release 3** adds migration verbs (`rename`, `backfill`, `map`). Each is a
  checkpointed, resumable batch, with a dry-run count in the proposal.

Records keep the `collection_version` they were written under.

### Storage
- **One platform schema, `app_data`:**
  - `records`: `app_id`, `collection`, `id`, `current_version`, system
    fields, and `doc` (JSONB, validated on write);
  - `record_versions`;
  - `definitions`: every version of every collection, view, page, template
    and the build notes, with `author`, `run_id` and `reason` stamped
    server-side;
  - `bundles` and `proposals`;
  - `build_ops`, the builder receipts.
- **Fixed indexes only:** `(app_id, collection, id)`,
  `(app_id, collection, created_at)`, and one GIN `jsonb_path_ops` on `doc`.
  There's no DDL at publish; row-scan caps bound every query.
- **Quotas** (platform-owned):
  - per App: records, bytes, writes per hour, views per page, scan rows per
    query;
  - per owner: Apps, open drafts, open proposals, and `build_ops` kept
    (90 days).

  Retired Apps keep counting until deleted.
- **Also covered:** backup export, pruning and health checks.
- **Why JSONB rows and not per-App tables:** agents evolve schemas often, and
  generated DDL would make every additive change a migration. Coded services
  remain for relational heavy lifting.

## Views

```json
{
  "view": "weekly_done",
  "collection": "habits",
  "filter": [{"field": "done", "op": "eq", "value": true},
             {"field": "day", "op": "within_last", "value": "12w"}],
  "group_by": [{"field": "day", "bucket": "week"}, {"field": "habit"}],
  "aggregates": [{"fn": "count", "as": "days_done"}],
  "fill_missing": true
}
```

- **Filters:** `eq`, `ne`, `in`, `lt`, `lte`, `gt`, `gte`, `is_null` and
  `within_last` in Release 1. Release 2 adds `contains` (escaped,
  case-insensitive) and `exists` / `not_exists` over referencing records,
  each with a sub-filter.
- **Parameters:** `{param: name}`, type-checked.
- **Sort and paging:** sort, limit ≤ 200, and paging.
- **Grouping:** ungrouped `count` (for a `metric`) is in Release 1.
  `group_by` and the other aggregates (`sum`, `min`, `max`, `avg`) arrive in
  Release 3.
- **Time:** each App has a timezone (default: the platform's
  `default_timezone` setting), and weeks start Monday.
  - `within_last: 12w` means the current, partial week plus the 11 full weeks
    before it: 12 buckets, each keyed by its Monday date.
  - The current bucket is flagged `partial: true`.
  - `fill_missing` emits a zero row for every bucket × every value of each
    other enum or bool `group_by` field in the window. It doesn't fill ref
    groups.
  - For the example: an empty week yields one zero row per habit; a habit
    never completed yields 12 zero rows; a record dated on a Sunday at 23:59
    local time counts in that week.
- **Counting:** `count` counts records. "Days completed" means one record per
  habit per day with a `unique` rule. The skill teaches that.

## Pages (`typed/v2`)

- **Renderer.** The `renderer` literal and `TypedBlock` kinds widen to
  `typed/v2`, and v2 pages are validated against App definitions (not
  `READ_FIELDS`). `LiveView.tsx` gains a v2 path, and an invalid v2 page
  returns the same 503 state.
- **Components** use `@ap/ui` tokens:
  - `table` (columns, formats, row links);
  - `detail` (one record, with history when versioned);
  - `metric` (one ungrouped count in Release 1);
  - `chart` (`bar` or `line`, explicit `x`, `y` and `series` bindings;
    Release 3);
  - `list_filter` (binds a view parameter);
  - `text` (headings and paragraphs as text; links only to the App's pages and
    platform pages).
- **Action templates**, a closed set:
  - `create {collection, presets, editable_fields}`;
  - `update {collection, presets, editable_fields}` against the current
    record, with `expected_version`;
  - `delete {collection}`;
  - `new_version {collection, copy_current: true, presets}` (Release 2).

  The first three ship in Release 1 because `delete: [kyle]` needs a UI.
- **Confirmation.** Clicking opens a server-generated confirmation showing
  the target record, the exact resulting values (presets included) and, for
  deletes, the delete plan.
- **Intent binding.** The intent binds the page version, a digest of the
  payload, the target's `version`, and for deletes the plan digest. At
  dispatch the server recomputes them; any difference refuses the call and
  asks Kyle to confirm again.
- **Contract and auth.** Execution runs under a new admitted contract,
  `app_data.write@1`, which generalizes the live-invocation flow to a
  collection/record target with its own argument digest. It requires Kyle's
  session at intent and dispatch.

`typed/v1` pages keep working unchanged.

## Tools

### `apps` (builder)
A Kyle-only grant. Every write takes a `request_id`. It is stored with a hash
of the arguments; a replay with the same hash returns the stored receipt,
and a different hash is refused. Receipts are kept in `build_ops`.

| action | what it does |
|---|---|
| `schema` | JSON Schemas for every definition kind, plus the kit's capabilities version. |
| `list` | Apps the caller owns or can read, each with status, open drafts and open proposals. |
| `get` | One App: approved state, drafts with revisions, open proposals, recent receipts, build notes, and health. |
| `create` | A new App, with a never-reused name and the narrowest approved state. |
| `draft` | Save one definition draft, with `expected_revision`. |
| `notes` | Read or write the App's build notes (≤ 4 KB), with `expected_revision`. |
| `validate` | Validate a bundle against the whole App and its records. Returns stable error codes with JSON paths, offending values and suggested fixes, the authority delta, and `stale_base` if the App moved. |
| `preview` | Render a draft page or run a draft view without side effects. For an unpublished collection it uses builder-supplied sample records, which are not persisted; otherwise real data the caller may read. `as: kyle` or `as: <agent>` renders exactly what that principal would see, never more than the caller can. Also dry-runs action templates. |
| `publish` | Publish a consistent bundle inside the approved state, with a compare-and-swap. Otherwise it refuses with the delta and suggests `propose`. |
| `propose` | Freeze a bundle as a proposal; returns its id and digest. |
| `proposal` | Read a proposal's status, or `withdraw` it. |
| `rollback` | Restore earlier definitions, under the same checks as `publish`. |
| `retire` | Hide an App and stop its views. Data and quota use persist; deletion is Kyle's. |

**Health**, shown by `get`, is computed on read: bindings that no longer
validate, records that violate a rule, and quota use. A daily job records it,
so the skill's maintenance check is cheap.

### `app_data` (records)
A Kyle-only grant; builders hold it. It serves an agent only for Apps whose
approved state names that agent, and only up to that agent's facts. Actions:

- `describe` — collections, fields and rules the caller may use;
- `query` — run a view with parameters;
- `get` — one record, with history;
- `create`, and `update` with `expected_version`;
- `delete` — only if the collection allows the caller;
- `delete_preview`.

Writes take `request_id` with an argument hash. Reads come back in an
untrusted-data block, and receipts carry ids only.

### Platform plumbing
- **Tool wiring:**
  - broker `@_metered` wrappers with rate limits and audit;
  - operation-catalog entries for both tools and `app_data.write@1`;
  - facade classification and SDK regeneration;
  - help topics.
- **Storage:** the quota store and the `app_data` schema migration.
- **Web:** a builder area (App list, drafts, proposals, review page) and a
  server route for live preview.
- **Naming:** `app_data` (records) is distinct from `query_app` (a coded
  service's GET proxy) and from `read_live_view_data` (the external facade).

## Automation (Release 3)

- **Maintenance:** builders schedule maintenance with Tasks and crons
  (design 37).
- **Data triggers:** a collection may declare `on_create` / `on_update`
  triggers that summon the owner agent with the record id, through an
  `app_data.events` topic. This is the first consumer of `entrypoints.topics`.
- **Approval:** a trigger is an authority fact, so adding one is a proposal.
- **Loops and cost:** events carry causal lineage, and a trigger never fires
  on a write whose lineage already contains it. Per-owner budgets cap runs,
  tokens and fan-out per hour.

## The App Builder request path

When a builder needs something the kit lacks, it searches open
`app-builder` tickets first and comments on a match. Otherwise it files one in
`#eng`:

```
Need: <the primitive: component, field type, rule, view feature, connector>
For: <App id and what its user is trying to do>
Tried: <the closest existing primitive and why it falls short>
Shape: <what the definition would look like if it existed>
```

It records the ticket key in the App's build notes. Before resuming, it
checks `apps schema`'s capabilities version, so it never assumes a primitive
shipped. It never ships a weaker App to work around a missing guard; it notes
the gap on the App's page instead.

## The `app-building` skill

A single `SKILL.md` (≤ 64 KiB) in the plugin release, assigned to Pai, Kai and
Olu. Formats and capabilities live in `apps schema`, so they evolve without a
skill release. Expect at least two skill releases: one with Release 1 and one
after the first real builds.

1. **Is an App the answer?** One-off answers go in chat, durable knowledge in
   the wiki, private facts in memory. An App is for something used
   repeatedly, viewed by Kyle, or maintained. Decide whether it's a report or
   a tool.
2. **The build procedure, with checkpoints:**
   1. `apps list` and `get`; read the build notes and open proposals.
      Resume, never duplicate.
   2. `schema`: check that the capabilities you need exist.
   3. Write the build notes: the goal, planned definitions, and the step
      you're on. This is the checkpoint before any mutation.
   4. Draft, then `validate` until clean.
   5. `preview` with sample records, then `as: kyle`, and dry-run each
      template.
   6. `publish`, or `propose` and record the proposal id in the notes. Waiting
      for approval is a checkpoint; stop the run there.
   7. After publish, write test records through `app_data`, then read them
      back as each intended principal, and verify the page renders.
   8. Update the notes: done, or the next step.
3. **Modeling:**
   - one collection per kind of thing;
   - write modes by asking what must never be rewritten;
   - `on_delete` by asking what a deletion should take with it;
   - `unique` for "one per day" facts;
   - bounded fields, minimal evidence;
   - per-field access for anything sensitive.
4. **Authority:**
   - what self-publishes;
   - new fields start private;
   - every rule is a guard;
   - writing a proposal Kyle can approve in thirty seconds;
   - never copy private data into a more widely visible App;
   - inspect existing records before tightening a rule.
5. **Maintenance:**
   - you own what you build;
   - check health when summoned and weekly (a Task);
   - fix forward; rollback restores definitions, not records;
   - retire stale Apps.
6. **Requests:** when and how to file an App Builder request.
7. **Worked examples:**
   - a reading list Olu owns and shares with Pai, by proposal (plus Kyle
     granting Pai `app_data`);
   - the habit log with its weekly chart and expected rows;
   - judgment's predictions collection.

## Judgment as the benchmark

| requirement (design 38) | primitive | release |
|---|---|---|
| typed records, enums, limits | collections | 1 |
| immutable predictions | `immutable` | 1 |
| Kai and Kyle only; session-only Kyle | access + `kyle` identity | 1 |
| only Kyle sets `kyle_confirmed` | `writer` rule with `value` | 1 |
| Kyle deletes on the page | `delete` template | 1 |
| versioned beliefs with history | `versioned` | 2 |
| predictions pin belief versions | `ref` + `pin_version` | 2 |
| pending = predictions with no resolving feedback | `not_exists` | 2 |
| confirm = copy current as Kyle-confirmed | `new_version` template | 2 |
| relayed requires a message reference | `required_when` + `message_ref` request | 3 |
| Kai can't reject a confirmed belief | `lock` over history | 3 |
| counts by outcome | grouped views | 3 |
| delete feedback → delete the belief versions citing it, rewind current | not covered (version-level cascade) | — |
| "mixed" on disagreement; 10-minute timing flags | not covered (derived logic) | — |
| latest-confirmed beside current in recall | not covered (needs a latest-matching-version view) | — |

About 75% is expressible by Release 3. **Judgment stays coded.** Release 4
rebuilds it on collections as a labelled evaluation, with acceptance cases
per release. Each uncovered row becomes an App Builder request.

## Trust boundaries and guards

- **No code execution:** definitions are data and components are
  host-owned. Text renders as text; external links render only by approved
  fact.
- **Escalation:**
  - Kyle-only tools; protected agents editable only by Kyle or themselves;
    no self-edits.
  - Frozen run tokens.
  - Owner, access, rules, templates, links and triggers change only by
    publish or approval, and widening always needs Kyle's session.
- **Leakage:** field-level access is checked at execution on every path,
  including predicates (no inference). New fields start private. Refs and
  reads never cross Apps.
- **Forged Kyle writes:**
  - Kyle approves each template;
  - the server-generated confirmation shows its exact effect;
  - intents bind version, payload, target version and plan;
  - Kyle's session is required.
- **Misleading or stale approvals:** the platform computes the delta,
  approval binds the digest, and approval re-validates atomically with a
  compare-and-swap.
- **Lifecycle tricks:** rollback runs publish's checks; ids are immutable and
  names never reused; quotas persist across retire; Apps whose owner is
  deleted transfer to Kyle.
- **Prompt injection:** agents read records in an untrusted block; approval
  cards carry platform text only.
- **Cost:** App and owner quotas, build-metadata caps, and trigger lineage
  and budgets.

## Rollout and effort

| release | contents | rough size |
|---|---|---|
| **0** Authority | Kyle-only tools, protected agents, no self-edits, audit, security test matrix; `owner_agent`, immutable ids, name reservation; broker-backed agent routes | ~6 tasks, 3–4 days |
| **1a** Build and store, owner-only | `app_data` store with `editable` / `immutable`, field-level access, refs (`restrict` / `unlink`), `writer` / `immutable_after_create` / `unique` rules; `app_data` tool; `apps` (schema, list, get, create, draft, notes, validate, preview, publish, rollback, retire); `typed/v2` table, detail and metric; the authority-fact engine with golden tests; quotas; skill v1. Kyle can view the App's pages; there are no Kyle write actions yet. | ~22 tasks, 4–5 weeks |
| **1b** Approval and sharing | proposals, digest and CAS, the review page and Relay card, sharing with agents, action templates (create / update / delete) with server-generated confirmations and `app_data.write@1`, outbound links | ~14 tasks, 2–3 weeks |
| **2** History and links | `versioned`, `pin_version`, `cascade`, `list`, `contains`, `exists` / `not_exists`, the `new_version` template, history in `detail` | ~10 tasks, 2 weeks |
| **3** Richer Apps | `required_when`, `lock`, migrations, grouping and aggregates, buckets, charts, `list_filter`, data triggers with lineage and budgets | ~15 tasks, 3 weeks |
| **4** Benchmark | Kai rebuilds judgment as an evaluation; each persona builds one real App | evaluation |

Release 1a alone is useful: an agent can build and maintain an App that it
and Kyle can see, such as a log, tracker or report it keeps itself.
Kyle-facing forms wait for 1b, because every template is a proposal.

## Alternatives considered

- **Agents write domain services through the coder.** That's today's path
  (judgment): a PR, a deploy and a large review per App.
- **Sandboxed author HTML/JavaScript.** Design 33 declined it for private
  data: a sandbox protects the host, not the data from the code.
- **Per-App generated tables.** Rejected for schema churn; coded services
  keep that option.
- **Publish everything, audit afterwards.** Rejected: the rules exist to
  protect Kyle from the builder.
- **Author-declared guards** (revision 1). Rejected: an owner could leave the
  flag off and remove the rule later. Every rule is a guard.
- **Collection-level read access only** (revision 2). Rejected: adding a
  field silently shared new data with every existing reader.

## Open decisions (Kyle)

1. **Builders:** only Pai, Kai and Olu, or any agent Kyle grants `apps` to?
2. **Kai's existing `agents_grant` and `agents_edit`:** keep them (now
   Kyle-only grants that can't touch protected agents), or remove them in
   Phase 0?
3. **Sharing beyond Kyle:** Apps visible to other human principals stay out
   of scope. Confirm.
4. **Start:** Release 0 + 1a first (about 5–6 weeks) to get a first
   agent-built App, then 1b?
