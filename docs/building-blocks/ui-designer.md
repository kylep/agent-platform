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
| `inspect` | one node's resolved geometry, colours and typography per mode, tokens used, text, component |
| `comments` | `op=list\|add\|resolve`: the thread agents and Kyle share, pinned to nodes or points |
| `history op=diff` | the writes between two version numbers, one line each |
| `export` | a full-size PNG artifact of a frame, plus a card in `#design` |
| `place` | copy an image artifact into the document and return the `sha256` an image node uses |

Every write publishes on the `ui_designer.events` topic, which feeds the
`/api/ui-designer/events` live stream the web UI refreshes from. An export, and
a version someone gives a label, also post a card to the `#design` channel (an
event row, so a frame title can never summon an agent).

The `ui-designer` [skill](skills.md) teaches the loop: outline, batched ops,
look at a snapshot.

**Contract:** every write carries a `request_id`; a retry with the same one
replays the receipt and applies nothing twice. Writes are metered per agent per
hour (writes and rendered megapixels). Lint warnings (text overflow, low
contrast, a coordinate on a laid-out child) come back with the write; they never
block it. Pictures go only to agent runs; Kyle and Codex over the external API get
text (`outline`, `get`, `edit` without snapshots) plus the web UI. Document
content is data, never instructions.

**Visual effects (Figma equivalents):** `fill` takes one paint or a list stacked
bottom to top (colour, `linear`/`radial`/`angular`/`diamond` gradient, or an
`image`, each with optional `opacity`); `stroke` adds `dash`, `cap`, `join` and
per-side `sides` widths; `shadow` is one or a list of drop and `inset` shadows
with `spread`; `blur` is a layer blur and `backdrop_blur` a background blur;
`blend` is a Figma blend mode under its CSS name. Limits: 8 paints per fill, 32
shadows, 32 blurred nodes, 8 backdrop blurs per document, blur and spread up to
64 px. Approximations (resvg is not a browser): `angular` and `diamond` are
stepped (72 wedges, 48 nested diamonds); `backdrop_blur` re-paints and blurs
what is already behind the node, clipped to its shape (not inside masks, and a
single-node render only sees that subtree); `sides` is inside-aligned without
dash; `plus-lighter` paints as `screen`; "plus darker" and "pass through" are
not offered (omit `blend` for the latter). Image paints go on frames, rects and
ellipses, and cannot be instance overrides.
