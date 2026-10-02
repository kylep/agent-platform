# App data

**What:** the platform-owned store behind agent-built [Apps](apps.md). An App
is rows: its identity, versioned definitions (collections, views and
`typed/v2` pages), build notes, and the records its collections hold. There
is no image, schema, deploy or per-App credential, so a fresh install has no
Apps and a database restore brings every App back
(`docs/design/39-agent-built-apps.md`). Builders write definitions with the
`apps` tool; agents read and write records with the `app_data` tool; Kyle
reads Apps and their pages from his browser session. Nothing an agent writes
here executes.

The [`app-building` skill](../agent-platform-coding-plugin.md) teaches agents
when to build an App and how; this page is the reference it relies on.

**Lives in:** Postgres, in `app_data_*` tables of the platform database:
`app_data_apps`, `app_data_definitions`, `app_data_records` (typed side
columns plus a JSON `doc`), `app_data_record_versions`, `app_data_build_ops`,
`app_data_staging_sets` and `app_data_staged_records`, `app_data_quotas`,
`app_data_scan_leases`, `app_data_write_counters`, `app_data_artifacts`,
`app_data_artifact_refs`, `app_data_tool_calls` and `app_data_proposals`. The code is
`services/backend/agentplatform/appdata/` and `api/app_data.py`.

Release 1a supplied the store and builder. Release 1b adds proposals,
sharing, action templates, tool views and App tool facts.

## An App

- **Identity:** an immutable id and a name (`^[a-z][a-z0-9_-]{0,47}$`) that
  is never reused, retired or not.
- **Owner:** the agent that created it, or `kyle`. When an owner agent is
  deleted its Apps pass to Kyle, and `owner` in its definitions now means
  Kyle.
- **Timezone:** an IANA name (default `UTC`). Dates, `within_last` windows
  and datetimes without an offset are read in it.
- **Status:** `active` or `retired`. Retiring hides an App and stops its
  views; its records, definitions and quota use stay, and deleting them is
  Kyle's call.
- **Approved version:** `null` until the first publish, then bumped by every
  publish and rollback. The **approved state** at version N is the newest
  published row at or below N for each definition. A removal is a tombstone
  row, so a rollback is the state at an earlier N published again as a new
  version.
- **Authority generation:** bumped with every approved version and
  ownership transfer.
- **Build notes:** at most 4,096 bytes, replaced whole under a revision
  compare-and-swap. They are the maintainer's runbook.

## The lifecycle

The builder works in **drafts**, one per definition name, each with a
revision and the approved version it was written against.

1. **Draft** saves one definition after checking it on its own
   (`AL-INVALID-DEFINITION` carries the issues). `expected_revision` is 0
   for a name with no draft yet. `remove: true` drafts dropping a published
   definition; `discard: true` throws a draft away.
2. **Validate** checks the App publishing would produce (the approved
   state overlaid with the drafts, or `only` some of them):
   - `errors`: definition-language issues, each with a stable code, a JSON
     path, the value, a suggested fix and the definition it belongs to;
   - `record_issues`: stored records the new definitions refuse
     (`AL-RECORDS-REQUIRED`, `AL-RECORDS-UNIQUE`, `AL-RECORDS-INVALID`,
     `AL-RECORDS-REF`, `AL-RECORDS-INDEX`), with counts and sample ids;
   - `data_dropping`: a removed collection that holds records, or a removed
     field that holds values;
   - `widening` and `delta`: the authority change (below);
   - `stale_base`: a draft written against an older version of its
     definition;
   - `publishable` and `suggest` (`"propose"` when it needs approval).
3. **Preview** runs a draft view or renders a draft page with no side
   effects. Unpublished collections read the builder's `samples` (at most 200
   per collection), inserted in a transaction that is always rolled back;
   published collections read real records. `as: "kyle"` or
   `as: "agent:<name>"` shows what that principal would see, never more than
   the builder may.
4. **Publish** is a compare-and-swap on `expected_approved_version` (`null`
   before the first publish). It refuses anything invalid (`AL-INVALID`),
   stale (`AL-STALE-BASE`), widening or data-dropping (`AL-NEEDS-PROPOSAL`)
   or inconsistent with stored records (`AL-INCONSISTENT`). It rebuilds the
   side columns of a collection whose indexed fields changed.
5. **Rollback** makes an earlier version current again, under publish's
   checks.
6. **Retire**, as above.

**Proposals.** A change that needs Kyle's approval is frozen by `apps
propose` with a fresh `request_id` and optional `only`, `rollback_to`, or
`transfer_to`. It returns a SHA-256 digest, authority delta, validation
summary, and base version. `apps proposal` reads or withdraws a proposal;
`apps get` lists open proposals for that App. Kyle's browser session can list
and inspect proposals at `/api/app-data/proposals`, then approve with the
shown digest or decline with a reason. Admin API keys and agent runs cannot
decide. Approval rechecks the frozen bundle against current records and
authority; a moved App or changed delta makes it `stale` without publishing.
Published, declined, withdrawn and stale proposals are final.

**Build ops.** Every write (lifecycle and records) takes a `request_id`,
stored with a hash of its arguments in the same transaction as the write.
The same id and arguments return the stored receipt with `"replayed":
true`, refusals included; the same id with different arguments is refused
(`AL-REQUEST-REUSED`).

**Health** is computed on read: approved definitions that no longer
validate (`invalid_bindings`), stored records breaking a `unique` or
`required` rule (`rule_violations`), and quota use. `status` is `failing`
with any issue and `warn` at 90% of the records or bytes limit it reports.

## Definitions

`apps schema` returns the JSON Schema of every kind and a `capabilities`
object (version 2), generated from `appdata/definitions.py`. Definitions are
closed: an unknown key is an error, never ignored. Definition names match
`^[a-z][a-z0-9_]{0,39}$`. An App holds at most 50 collections, 100 views,
50 pages and 20 App tools.

### App tools

An App tool (kind `tool`, under `app_tools` in a bundle) says which of the
App's collections a tool reaches: `{"tool": "<name>", "roles": {"<role>":
{"collection": "<collection>", "verbs": [...]}}}`. Each role is one the
tool's `app_access` declares; the collection must be in the App, and the
verbs are from read, create, update and delete (an immutable collection has
no update). Adding an App tool, or widening its verbs, is a proposal;
narrowing or removing one self-publishes. The builder area shows them read
only.

### Collections

| key | meaning |
|---|---|
| `collection` | the name |
| `description` | at most 500 characters |
| `fields` | 1–64 fields, `{name: spec}` |
| `write_mode` | `editable` (default) or `immutable` |
| `access` | `read`, `create`, `update`, `delete`: principal lists |
| `writers` | tool-only verbs: `{create\|update\|delete: ["tool:<name>"]}` |
| `rules` | up to 20 |
| `indexed` | up to 4 field names |
| `retention` | exactly one of `max_age` (`90d`, `12w`, at most ten years) or `max_records` |

Every field spec takes `type`, `required` (default false), `label`,
`description` and `access` (per-field `read`, `create`, `update`).

| type | value | extra keys |
|---|---|---|
| `string` | text, at most 1,000 characters | `min`, `max` |
| `text` | text, at most 16,000 characters | `min`, `max` |
| `int` | a whole number within ±2⁵³−1 | `min`, `max` |
| `number` | a finite number | `min`, `max` |
| `bool` | `true` / `false` | |
| `date` | `YYYY-MM-DD` | |
| `datetime` | ISO 8601; no offset means App time; stored and returned in UTC | |
| `enum` | one of `values` (1–100 distinct) | `values` |
| `ref` | a record id in `collection` of the same App | `collection`, `on_delete` (`restrict` default, `unlink`) |
| `url` | text, at most 2,000 characters | `max`, `link` (render as an outbound link) |
| `artifact` | an artifact id (below) | |

**System fields** are on every record and can't be written or reused as
field names: `id`, `created_at`, `updated_at`, `author`, `via`, `version`,
`collection_version`.

**Rules:**
- `{"kind": "unique", "fields": [...]}` on up to four fields, checked under
  a per-collection lock (`pg_advisory_xact_lock` on Postgres);
- `{"kind": "writer", "field": f, "writers": [...]}` with an optional
  `value`: only those principals may set the field (to that value);
- `{"kind": "immutable_after_create", "fields": [...]}`: refused on an
  `immutable` collection, where it would be redundant.

**Indexed fields** are copied into typed side columns served by fixed
composite indexes: two text-like fields (`string`, `enum`, `ref`) into
`ix_text1` then `ix_text2`, one `int` or `number` into `ix_num1`, and one
`date` or `datetime` into `ix_time1`. The indexes are
`(app, collection, ix_text1, ix_time1)`,
`(app, collection, ix_text1, ix_text2, ix_time1)`,
`(app, collection, ix_text1, ix_num1)`, `(app, collection, ix_time1)` and
`(app, collection, created_at)`, plus a GIN index over `doc` on Postgres.
An indexed text value is at most 256 characters.

### Views

| key | meaning |
|---|---|
| `view`, `collection` | the name, and the one collection it reads |
| `fields` | fields returned (default: all, system fields included) |
| `params` | up to 10, `{name: {type, required, default}}`; types `string`, `int`, `number`, `bool`, `date`, `datetime` |
| `filter` | up to 10 `{field, op, value}` |
| `sort` | up to 3 `{field, dir}`; default `created_at` descending |
| `limit` | 1–200, default 50 |
| `paging` | keyset paging with an opaque `cursor` |
| `aggregates` | exactly one `{"fn": "count", "as": name}` makes a count view |

Filter operators: `eq`, `ne`, `lt`, `lte`, `gt`, `gte` (ordered types),
`in` (a literal list of 1–50), `is_null` (`true`/`false`), `contains`
(case-insensitive substring of `string`, `text` or `url`, with `%`, `_` and
`\` escaped) and `within_last` (`<n>h|d|w|m|y` on a date or datetime;
hours only on datetimes). `within_last` counts in the App's timezone with
Monday weeks: `12w` is the current partial week plus the eleven full weeks
before it. `anchor: "max(<field>)"` measures back from the newest value among
the records the other filters select. A filter value is a literal of the
field's type or `{"param": name}`; an unset optional parameter with no
default drops the filters that use it. Every declared parameter must be
used. A count view takes no `fields`, `sort`, `limit` or `paging`.

### Pages

A `typed/v2` page has a `page` name, a `title`, optional `params` (up to 8)
and up to 50 `blocks`:

| block | keys |
|---|---|
| `text` | `style` (`heading`/`paragraph`), `text` (≤ 4,000), optional `link` to `{page}` of this App or `{path}` on the platform |
| `metric` | `label`, a count `view`, `params` |
| `table` | a record `view`, `title`, 1–12 `columns` (`field`, `label`, `format`), `params`, optional `row_link: {page, param}` |
| `detail` | a record `view`, `title`, `fields`, `params`: the first record the view returns |

Column formats: `auto`, `text`, `number`, `percent`, `date`, `datetime`,
`relative_time`, `bool`. Block `params` bind each view parameter to a literal
or to `{"page_param": name}`; every required view parameter must be bound. A
`row_link` opens another page of the App, passing the row's id as a string
page parameter. Pages also accept `actions` (create, update and delete
templates) and blocks reference them, but a template is an authority fact
that needs a proposal, and the renderer doesn't run them until `app_data.write@1`
ships (Release 1b).

### Example

```json bundle
{
  "collections": [
    {
      "collection": "readings",
      "fields": {
        "sensor": {"type": "enum", "values": ["kitchen", "garage"], "required": true},
        "taken_at": {"type": "datetime", "required": true},
        "celsius": {"type": "number", "required": true, "min": -40, "max": 60}
      },
      "write_mode": "immutable",
      "rules": [{"kind": "unique", "fields": ["sensor", "taken_at"]}],
      "indexed": ["sensor", "taken_at"]
    }
  ],
  "views": [
    {
      "view": "latest",
      "collection": "readings",
      "params": {"sensor": {"type": "string", "required": true}},
      "filter": [
        {"field": "sensor", "op": "eq", "value": {"param": "sensor"}},
        {"field": "taken_at", "op": "within_last", "value": "24h"}
      ],
      "sort": [{"field": "taken_at", "dir": "desc"}],
      "limit": 48
    },
    {
      "view": "readings_today",
      "collection": "readings",
      "filter": [{"field": "taken_at", "op": "within_last", "value": "1d"}],
      "aggregates": [{"fn": "count", "as": "n"}]
    }
  ],
  "pages": [
    {
      "page": "temperatures",
      "title": "Temperatures",
      "blocks": [
        {"kind": "metric", "label": "Readings today", "view": "readings_today"},
        {"kind": "table", "view": "latest", "params": {"sensor": "kitchen"},
         "title": "Kitchen, last 24 hours",
         "columns": [{"field": "taken_at", "format": "relative_time"},
                     {"field": "celsius", "format": "number"}]}
      ]
    }
  ]
}
```

## Access and authority

**Principals** in definitions: `owner` (resolved to the App's owner at check
time, so access follows a transfer), `kyle`, `agent:<name>` and `login:qa`
(read only). A tool is never an access principal; tool-only writes are
`writers`. Collection defaults: read `owner` and `kyle`; create, update and
delete `owner`. A field's `access` overrides `read`, `create` or `update`
for that field; `delete` is collection-wide.

Access is checked per field for the actual caller on every path: results,
filters, sorts, anchors, previews and the artifact routes. A caller who can
read the collection or any of its fields sees its rows; a field it can't
read comes back `null` and is named in the row's `restricted` list. Using an
unreadable field in a predicate is refused (`AD-PREDICATE-FORBIDDEN`),
because a predicate answers questions about the value. `author` and `via`
follow the collection's read default; the other system fields follow the
row.

**Authority facts** (`appdata/authority.py`) are computed from the
definitions: field access `(principal, collection, field, verb)`, record
delete, delete reach through refs, retention, rules (including tool-only
writers), action templates, outbound links, App tools, and, in shapes the
language doesn't accept yet, tool views, tool actions and service
principals. `apps authority` prints them in plain words with a digest.

A bundle **self-publishes** when every fact it computes is in the approved
state, or narrower. A new App's approved state is empty, so its first
publish may grant only `owner` and `kyle`. Everything else is a widening:
- a reader or writer beyond owner and Kyle;
- retention (any, on a new collection; a shorter one, on an existing one);
- `on_delete: unlink` (`restrict` reaches nothing and is free);
- `link: true`;
- an App tool, or more verbs on one;
- `writers` for a tool that isn't an approved App tool;
- action templates;
- relaxing a rule (removing it, growing a `writer` rule's writers, freezing
  fewer fields; any change to a `unique` rule);
- a definition key the engine can't map to a fact ("unknown means
  proposal").

Adding a rule always self-publishes, after validate checks it against
stored records. **New fields start private:** publish stores a field added
to an existing collection with explicit access cut to owner and Kyle for
every verb, so a field added to a shared collection reaches no one new.

Widening and data-dropping changes need a proposal Kyle approves. Proposals
arrive in Release 1b; until then `publish` refuses them with
`AL-NEEDS-PROPOSAL`, and an App stays visible to its owner and Kyle only.

## Records

- **Writes** take a `request_id` and name only declared, writable fields:
  a system field is `AD-SYSTEM-FIELD`, an unknown one `AD-UNKNOWN-FIELD`,
  one the caller may not write `AD-FIELD-FORBIDDEN`. Values are checked
  against type and bounds (`AD-INVALID-VALUE`) and stored normalized; an
  unset field is absent, not `null`. A create returns `{collection, id,
  version}`.
- **Updates** patch the fields given, under `expected_version`
  (`AD-VERSION-CONFLICT` otherwise). `null` clears a field unless it's
  required (`AD-REQUIRED`). Each update keeps the previous version in
  `app_data_record_versions`. An `immutable` collection refuses updates
  (`AD-IMMUTABLE`), and `immutable_after_create` fields refuse changes
  (`AD-RULE-IMMUTABLE-FIELD`).
- **Rules** refuse with `AD-UNIQUE`, `AD-RULE-WRITER`; a ref to a missing
  record is `AD-REF-MISSING`.
- **Deletes** run a server-computed plan in one transaction. A record still
  referenced through a `restrict` ref blocks the delete (`AD-REF-RESTRICT`,
  with the plan); `unlink` refs are cleared. `delete_preview` returns the
  plan for up to 100 ids without deleting.
- **Reads** return `{id, values, restricted}` per row.
- **Retired Apps** refuse writes (`AD-APP-RETIRED`) and their views.

A view result is `{rows, next_cursor, as_of, stale}`, or `{count, as_of,
stale}` for a count view. Parameters given as query-string text are coerced
to their declared types. `limit` overrides the view's own, up to 200; a
cursor belongs to one view (`AD-CURSOR`).

## Batch writes

The records engine has batch writes (`appdata/batch.py`); neither the
`app_data` tool nor an HTTP route exposes them yet. One `batch` writes up to
5,000 records or 5 MiB into one collection in one transaction, through the
same checks as a single write:
- `insert`: every record is new;
- `upsert`: matched on a `unique` rule (`key` names it when there are
  several); an `editable` match is patched with history, an `immutable`
  match is replaced whole only when its values differ, with no history;
- `skip_existing`: a record clashing with a `unique` rule is skipped and
  answers with the existing id.

Errors are per record; by default one error rejects the whole call
(`AD-BATCH-REJECTED`, listing every error found), and `on_error: "skip"`
commits the rest. **Batch jobs** stage larger writes in a staging set bound
to its creator and the collection versions it opened against. Staged records
are invisible; the commit re-validates schema versions, authority and quotas
and publishes everything in one transaction, or nothing. Sets expire after
24 hours.

## Artifacts in records

An `artifact` field's value is an artifact id. Writing one makes the
artifact **App-owned**: its first referencing field owns it, and another
field may reference it only if its readers are a subset of the owning
field's (`AD-ARTIFACT-WIDENS`). From then on the byte route, metadata, list,
stats and the artifact event feed authorize it through the owning field's
read access, not the `artifacts` grant. Only the artifact's own agent or
Kyle can put an ordinary artifact into a field, never one an agent wears as
its face (`AD-ARTIFACT-IN-USE`), and an App-owned artifact can't move to
another App; any id the caller can't use is `AD-ARTIFACT-UNAVAILABLE`. App
artifacts count against the App's bytes, up to 64 MiB each, and are deleted
in the same transaction as their last reference.

## Quotas

Limits are platform-owned and fail closed: an App's definitions can't move
them. A limit nobody has set is the config default (`AP_APP_DATA_*`):

| limit | per App | per owner |
|---|---|---|
| records | 250,000 | 5,000,000 |
| bytes (record JSON plus App artifacts) | 256 MiB | 4 GiB |
| writes per hour (records created or changed) | 200,000 | 1,000,000 |
| scan rows per hour | 20,000,000 | 60,000,000 |
| concurrent scans | 2 | |
| Apps | | 20 |
| open drafts / open proposals | 100 / 5 | — / 20 |

One scan reads at most 1,000,000 rows in 60 seconds. Storage and writes
are charged in the write's own transaction, App row then owner row, so two
writers can't both spend the same headroom. A breach is `AD-QUOTA-RECORDS`
or `AD-QUOTA-BYTES` (413) or `AD-QUOTA-WRITES` (429 with `retry_after`),
with a `detail` naming the scope, limit, maximum and use. Usage is released
only by deletes; a retired App keeps counting. Kyle raises a scope's limits
with `quotas.set_quota`, which has no HTTP route yet. The Apps and drafts
limits are defined but not yet checked by `create` and `draft`.

## Tools

Both tools are **Kyle-only grants** (`KYLE_ONLY_TOOLS`): only Kyle's browser
session can add them to or remove them from an agent, and an agent holding
one is editable only by Kyle or itself ([security.md](security.md)). They
call the agent routes `POST /api/app-data/agent/…`, which answer only an
agent run: the run's frozen grants must hold the tool, and a caller with no
run (an admin API key) is refused. Errors are
`{"detail": {"code", "message", "detail"}}`.

| tool | actions (route suffix) |
|---|---|
| `apps` | `apps/schema`, `list`, `create`, `get`, `draft`, `notes`, `validate`, `preview`, `publish`, `rollback`, `retire`, `authority`, `health` |
| `app_data` | `records/describe`, `query`, `get`, `create`, `update`, `delete`, `delete_preview` |

`apps` acts only on Apps the caller owns (`AL-NOT-OWNER`), except `list`,
which also shows Apps whose approved facts let the caller read.
`app_data` runs every call through the records engine as the caller, so the
App's approved facts decide.

A tool declaring `app_access` gets a tool-call credential per call
([tools.md](tools.md#app-access-tool-call-credentials)). Its scope in each
App comes only from that App's approved App tool for the tool: no App tool,
no scope, whatever the collections are called.

## Kyle's routes

Kyle's browser session (`X-AP-Auth: session`, role `admin`) reads every App;
an admin API key, another login or an agent gets 403. Errors are
`{"detail": "<text>"}`.

| route | returns |
|---|---|
| `GET /api/app-data/apps` | every App, retired included, with health |
| `GET /api/app-data/apps/{app}` | approved definitions, drafts, build notes, health, recent build ops |
| `GET /api/app-data/apps/{app}/pages/{page}` | the published page's `typed/v2` definition |
| `GET /api/app-data/apps/{app}/views/{view}?<param>=…&limit=&cursor=` | a view result as Kyle |

`{app}` is the id or the name. In the console, `/apps` lists state Apps,
`/apps/state/<id>` is the read-only builder area (definitions, drafts,
notes, health), and `/apps/state/<id>/pages/<page>` renders a page. A page
whose App no longer validates answers 503 (`AD-DEFINITION-INVALID`), like a
broken `typed/v1` page.

## Error codes

Codes are stable: a code never changes meaning.

- **`JD-*`**, definition language (`appdata/errors.py`):
  - shape: `JD-UNKNOWN-KEY`, `JD-MISSING`, `JD-TYPE`, `JD-BOUNDS`,
    `JD-VALUE`, `JD-NAME`, `JD-FORMAT`, `JD-INVALID`;
  - releases: `JD-NOT-YET-R2`, `JD-NOT-YET-R3` (designed, not built) and
    `JD-REQUEST-PATH` (not planned: file an App Builder request);
  - collections: `JD-FIELD-*`, `JD-ENUM-VALUES`, `JD-PRINCIPAL*`,
    `JD-WRITERS`, `JD-REF-*`, `JD-RULE-*`, `JD-INDEX-*`, `JD-RETENTION`;
  - views: `JD-VIEW-*`, `JD-FILTER-*`, `JD-PARAM-*`, `JD-SORT-DUPLICATE`;
  - pages: `JD-PAGE-*`, `JD-TEMPLATE-*`, `JD-PRESET-VALUE`;
  - bundles: `JD-DUPLICATE-NAME`.

  Each issue has `code`, `path` (a JSON path), `message`, `value` and `fix`.
- **`AL-*`**, lifecycle (`appdata/lifecycle.py`):
  - requests: `AL-REQUEST-ID`, `AL-REQUEST-REUSED`, `AL-CONFLICT`, `AL-ARGS`;
  - Apps: `AL-NO-APP`, `AL-NOT-OWNER`, `AL-APP-RETIRED`, `AL-NAME`,
    `AL-NAME-TAKEN`, `AL-TIMEZONE`, `AL-DESCRIPTION`;
  - drafts and notes: `AL-KIND`, `AL-INVALID-DEFINITION`, `AL-NAME-MISMATCH`,
    `AL-STALE-REVISION`, `AL-NO-DRAFT`, `AL-NOT-PUBLISHED`,
    `AL-NOTES-TOO-LONG`;
  - publishing: `AL-INVALID`, `AL-STALE-BASE`, `AL-NEEDS-PROPOSAL`,
    `AL-INCONSISTENT`, `AL-NOTHING-TO-PUBLISH`, `AL-ROLLBACK-TARGET`, and
    the `AL-RECORDS-*` record issues;
  - preview: `AL-PRINCIPAL`, `AL-SAMPLES`, `AL-SAMPLES-PUBLISHED`,
    `AL-NO-DEFINITION`.
- **`AD-*`**, records, views, batch, artifacts and quotas:
  - access: `AD-FORBIDDEN`, `AD-FIELD-FORBIDDEN`, `AD-PREDICATE-FORBIDDEN`,
    `AD-TOOL-ONLY`, `AD-OUT-OF-SCOPE`;
  - lookup: `AD-NO-APP`, `AD-NO-COLLECTION`, `AD-NO-VIEW`, `AD-NO-PAGE`,
    `AD-NOT-FOUND`, `AD-APP-RETIRED`, `AD-DEFINITION-INVALID`;
  - values and rules: `AD-INVALID-VALUE`, `AD-SYSTEM-FIELD`,
    `AD-UNKNOWN-FIELD`, `AD-REQUIRED`, `AD-IMMUTABLE`, `AD-VERSION-CONFLICT`,
    `AD-UNIQUE`, `AD-RULE-WRITER`, `AD-RULE-IMMUTABLE-FIELD`,
    `AD-REF-MISSING`, `AD-REF-RESTRICT`, `AD-INDEX-TOO-LONG`;
  - views: `AD-PARAM-UNKNOWN`, `AD-PARAM-TYPE`, `AD-PARAM-REQUIRED`,
    `AD-LIMIT`, `AD-CURSOR`, `AD-SCAN-COUNT-VIEW`;
  - batch: `AD-BATCH-*`, `AD-STAGING-*`;
  - artifacts: `AD-ARTIFACT-*`, `AD-NOT-ARTIFACT-FIELD`;
  - quotas: `AD-QUOTA-*`.

## Not built yet

| release | adds |
|---|---|
| R1a, still open | the `apps` and `app_data` broker tools; batch through the tool; the daily retention, staging-set and build-op sweeps |
| R1b | proposals and Kyle's review page; sharing; action templates and `app_data.write@1`; tool views, scans, cache and materialization; App tool facts; tool-only collections; links; restore maintenance mode |
| R2 | `versioned` collections, `list` fields (including objects), `pin_version`, `cascade`, `exists`, `new_version`, record history on `detail`, tool actions on pages |
| R3 | grouping and aggregates beyond `count`, `fill_missing`, windows, `chart`, `calendar`, `sparkline`, `stat_row`, `list_filter`, `image`, `refresh` |

`required_when`, `lock`, data triggers, schema migrations, and export and
import aren't planned: they come back only through an App Builder request.
