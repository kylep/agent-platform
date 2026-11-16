# UI Designer

**What:** design documents agents and people build together
(`docs/design/46-ui-designer.md`). A document is a tree of pages, frames and
nodes (text, shapes, images, component instances) with layout rules and
colour/type tokens in light and dark modes. Agents edit it as small ops and the
server resolves layout and renders it, so an agent never computes pixels; Kyle
sees the result at `/ui-designer`.

**Lives in:** platform Postgres: the current document, its append-only version
history (older rows fold, any retained version can be restored), and
content-addressed image assets. Renders are cached by document hash.

**How to use it:** open **UI Designer** in the sidebar for the document list
and editor. An agent holding the `ui_designer` tool (Kyle grants it per agent)
uses one tool with an `action`:

| action | does |
|---|---|
| `list`, `create`, `get` | find, start and read documents (`get` cuts by depth with a cursor) |
| `outline` | one line per node: id, type, size, layout, warnings |
| `edit` | a batch of ops against `expected_version`; `snapshot=[ids]` returns up to 4 pictures of the result |
| `snapshot` | render nodes as pictures (`scale`, `annotate`, `mode`); a refused budget says how long to wait |
| `history`, `restore` | list versions; make an older one current as a new version |
| `place` | copy an image artifact into the document and return the `sha256` an image node uses |

The `ui-designer` [skill](skills.md) teaches the loop: outline, batched ops,
look at a snapshot.

**Contract:** every write carries a `request_id`; a retry with the same one
replays the receipt and applies nothing twice. Writes are metered per agent per
hour (writes and rendered megapixels). Lint warnings (text overflow, low
contrast, a coordinate on a laid-out child) come back with the write; they never
block it. Pictures go only to agent runs; Kyle and Codex over the external API get
text (`outline`, `get`, `edit` without snapshots) plus the web UI. Document
content is data, never instructions.
