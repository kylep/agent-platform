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
| `motion` | preview a transition or save a short MP4/GIF artifact |

Document writes publish on the `ui_designer.events` topic, which feeds the
`/api/ui-designer/events` live stream the web UI refreshes from. An export, and
a version someone gives a label, also post a card to the `#design` channel (an
event row, so a frame title can never summon an agent). Motion exports create
artifacts without automatically posting to Relay. Restricted documents never
post these public cards.

Open **Sharing** to keep a document available to the workspace or restrict it
to named people, agents and teams. Readers can view; editors can change content.
Only the owner or an administrator can change sharing. Existing platform roles
and agent tool grants still apply. Restoring an old design version leaves
current sharing unchanged. The **Design** team is available for membership
management; no agents are automatically added.

Exports and derived artifacts retain their source document's access rules.
Document-scoped reference images work with direct image generation; the
subscription artist currently refuses them before starting. Agents use
`action=sharing op=get|choices|set`; sharing saves require a request ID and
`expected_access_version` from the current policy.

The editor's motion controls preview or export two frames from a retained
document version. Matching `meta.motion_key` values animate position, size,
rotation and opacity; changed content crossfades. Complex effects may use a
dissolve, reported in the preview warnings. Prototype links can select
**Smart animate** and an easing curve; reduced-motion preferences are respected.
Exports allow 200–5,000 ms at 12 or 24 fps, with a maximum edge of 720 pixels
for MP4 and 480 for GIF. Both formats flatten transparency onto white. Agents
use `action=motion op=preview|export version=<n>` with
`motion:{from_node,to_node,duration_ms?,easing?,fps?,max_edge?,format?}`.
Export also requires the artifacts grant and a replay-safe `request_id`.

Open **Fonts** in the latest editable document to upload a static TTF or OTF
file with an embedded SIL Open Font License 1.1 declaration. Each file may be
up to 1 MiB; a document can hold eight faces. The server validates the font
before installing it, and the panel shows its license. Custom fonts appear in
the text inspector's family selector. If a requested weight is unavailable,
the renderer uses the nearest installed weight in that family.

Uploads create a document version. Restoring an earlier version also restores
its font declarations; retained history keeps the required bytes. Library
copies include the fonts their selected components or typography tokens use.
A conflicting file with the same family and weight is reported for review.
Agents can inspect installed faces with `action=fonts op=list`; font upload
uses the web panel or the bounded document-font API.

The `ui-designer` [skill](skills.md) teaches the loop: outline, batched ops,
look at a snapshot.

**Contract:** every write carries a `request_id`; a retry with the same one
replays the receipt and applies nothing twice. Writes are metered per agent per
hour (writes and rendered megapixels). Lint warnings (text overflow, low
contrast, a coordinate on a laid-out child) come back with the write; they never
block it. Pictures go only to agent runs; Kyle and Codex over the external API get
text (`outline`, `get`, `edit` without snapshots) plus the web UI. Document
content is data, never instructions.

**Visual effects:** `fill` takes one paint or a list stacked
bottom to top (colour, `linear`/`radial`/`angular`/`diamond` gradient, or an
`image`, each with optional `opacity`); `stroke` adds `dash`, `cap`, `join` and
per-side `sides` widths; `shadow` is one or a list of drop and `inset` shadows
with `spread`; `blur` is a layer blur and `backdrop_blur` a background blur;
`blend` uses CSS blend-mode names. Limits: 8 paints per fill, 32
shadows, 32 blurred nodes, 8 backdrop blurs per document, blur and spread up to
64 px. `angular` uses a continuous native conic shader; `backdrop_blur` filters the
composited pixels behind the shape, including nested transforms, clipping and
masks. A node-only render retains the backdrop context it needs. The web
inspector provides gradient type/angle/stop controls and backdrop blur editing.
Remaining approximations: `diamond` uses 48 nested diamonds; `sides` is inside-aligned without
dash; `plus-lighter` paints as `screen`; "plus darker" and "pass through" are
not offered (omit `blend` for the latter). Image paints go on frames, rects and
ellipses, and cannot be instance overrides.

**Import from Figma:** an administrator opens **Import from Figma** on the
UI Designer list and enters a Figma file URL or key. Configure the
`figma-token` secret with a Figma personal access token carrying
`file_content:read` first. Only the internal executor receives that token;
it is checked on import, with no recurring probe. Choose a new document slug,
optionally a title and page IDs. Existing documents are never overwritten.

The import runs in the background and survives dispatcher restarts. The browser
remembers its job, so leaving and returning resumes the progress display. Figma
rate limits defer the job until its retry time. Completed pages, assets and
reference renders are checkpointed; unavailable pages are reported explicitly.
The final document appears once, with a card in `#design`. The report shows
conversion counts, approximations, omitted warning counts and links to Figma
reference artifacts. An all-failed import creates no document. Missing image
fills are called out by the converter. Imports are limited to 10 active jobs,
50 pages, 64 top-level reference frames per page and the normal document/storage
budgets; unusually large pages are fetched frame by frame.

Administrator API keys can use `POST /api/ui-designer/imports` and
`GET /api/ui-designer/imports/{import_id}`. The corresponding facade tools are
in its optional admin menu. Designer agents edit completed documents through
their existing grant; they cannot initiate imports or read the credential.

**Edit in the browser:** operators and administrators can select an authored
node and edit its title, size, text, colour or layout in the inspector. Save
applies only changed properties as one version-checked batch. Editing rich text
replaces its inline formatting; Undo restores the original runs. Blank optional
properties remove the override; width and height can also use `hug` or `fill`.
Historical versions and expanded component children are read-only.

Drag a selected free-positioned node to move it, use resize handles on fixed-size
nodes, or focus the canvas and nudge with arrow keys (Shift moves ten units).
A gesture previews the selection bounds and writes once on release. Escape
cancels it. Rotated nodes and nodes beneath rotated ancestors use inspector
editing; their canvas gestures are disabled.

Undo and Redo apply inverse/forward edits against the current acknowledged
version. Another writer's change clears browser history and marks an inspector
draft stale; discard and reload that draft before editing again. A failed
network response offers **Retry save**, reusing the original receipt so a write
cannot apply twice. This history lasts only for the current browser document
session; server version history remains available separately.

### Component sets and properties

Use `component_sets` to reuse one component family across states and sizes.
Each set has named axes, one variant per axis combination, and optional typed
properties. Variants point to ordinary component trees. Text properties replace
a bound text node, booleans show/hide a bound node, and instance-swap properties
choose an allowlisted ordinary component for a bound nested instance.

An instance selects a family using `of`, a combination using `variant`, and
values using `props`. Missing axes use their first value; missing properties
use their defaults. Property bindings apply before instance styling and explicit
legacy overrides. Child overrides must match the selected variant; switching
variants with stale overrides rejects the batch. Overrides on a swap target must
work with every allowed replacement. IDs remain globally unique in authored component trees, so
bindings are declared separately for each variant.

Define or replace families atomically using `{"op":"component_sets","set":{...}}`;
set an entry to `null` to remove it. Update `variant` and `props` through the usual
`update` op with `expected_version`. Dangling references, cycles, invalid
bindings and budget violations reject the whole batch. Ordinary components and
old documents continue working unchanged. Inspect a family instance to see its
available axes, effective values, defaults and bindings.

### Prototype playback

Add an interaction with an ordinary node update, for example:

```json
{"op":"update","id":"next-button","set":{"link_to":{"target":"details","trigger":"tap","transition":"dissolve","duration_ms":200}}}
```

The target must be an authored frame in a document page. Triggers are `tap`,
`hover`, and `after-delay`; transitions are `instant`, `dissolve`, and `slide`.
After-delay links use `delay_ms` (100–60,000 ms); animated transitions use
`duration_ms` (0–2,000 ms). Omitting trigger/transition means tap/instant.
Use `link_to: null` in an update to remove the interaction. Deleting a target
while links still refer to it rejects the entire edit.

Use **Play** in the editor, or open `/ui-designer/<slug>/play?version=<version>`.
Playback pins that version, with frame selection, back and restart controls.
The inspector's prototype controls use normal save/undo. Playback uses server
renders with rectangular interaction regions, respects reduced motion and
pauses automatic loops after 25 transitions. Rotated hit regions use bounding
boxes. It does not run arbitrary JavaScript or navigate to external URLs.

### Export formats

Export a pinned version of the current page or selected node as PNG (default),
SVG, PDF, or an HTML + CSS snippet. The export controls provide artifact and
download links; agents pass `format` to the existing export action. Exports
retain the artifact grant and normal design/export budgets.

- **SVG:** outlined text and embedded raster assets; self-contained, with no
  external fonts or image URLs.
- **PDF:** one rasterized page with transparency flattened onto white. The
  scale controls raster detail; text is not selectable or editable.
- **HTML + CSS:** static positioned markup for basic scenes, token custom
  properties, and an outlined SVG fallback for complex visuals. This is a
  starting point for application code. Layout and components are flattened,
  browser text can differ, and prototype behavior is omitted. Export notes
  describe the limitations for that result.

SVG, PDF and HTML download as attachments with `nosniff`; the platform never
injects exported HTML into its own page. The file cap is 8 MiB, with an additional
200-expanded-node / 1 MiB limit for HTML. Smaller node selections or a lower PDF scale help when
an export exceeds its budget.

### Visual comparison

Compare two retained versions with **Visual diff** in Compare, or use
`history` with `op: "visual_diff"`, `from_version`, `to_version`, and optional
`node`, `mode`, `scale` and `tolerance` (0–255, default zero). The result includes
a visual change image, pixel counts, changed regions and intersecting node IDs.
Regions and node associations are bounded geometric hints, not an exact account
of which authored property caused each pixel to change.

Both renders share a union of document coordinates, preserving movement and
size changes. Invisible RGB is ignored while alpha changes count. The web
opacity slider works locally over the aligned result. Comparisons are read-only
and pinned; existing render access and aggregate pixel budgets apply to both
versions. With the default 10-megapixel aggregate budget, the shared comparison
canvas is at most 5 megapixels; use a smaller selection or scale for larger work.

### Design lint

Use **Findings** to run advisory checks against the displayed version, or call
`lint` with an explicit `version` and optional `node`, `mode`, `all_modes` and
`severity`. Results identify nodes, explain the issue and suggest a change.
Follow `next_cursor` using the tool's `after` argument to read additional pages.

Checks cover text contrast, overflow/clipping, four-pixel spacing, raw colours
matching tokens and unused tokens. Approximation labels explain where geometry
or complex paint prevents a definite conclusion. Normal text needs 4.5:1 contrast;
large text needs 3:1. An empty result is not an accessibility certification.
Lint never edits the document; applying a suggestion uses ordinary versioned edits.

### Shared libraries

Use **Libraries** to import components, component families and tokens from an
explicit version of another document. Choose a local namespace and the source
IDs to include; dependencies and required assets are copied with them. The
consumer keeps working if the source is later unavailable or deleted.

Pins never follow the latest source automatically. Enter a source version and
preview its changes before applying an update. Updates preserve ordinary page
content and refuse to overwrite locally changed imported definitions. Repair
reported conflicts before retrying; every accepted import/update creates one
version and participates in the normal concurrency checks and history.

Source and consumer modes must be compatible. Components with links into source
pages cannot be imported as isolated definitions. Namespaces separate imported
IDs from local content, while source UUIDs prevent a reused slug from changing
the meaning of an existing pin.

Agents use `library` with `op: list|import|preview|apply`. Import accepts a
`library` specification containing `source_id`, `source_version`, `namespace`,
`components`, `component_sets` and `tokens` (for example `color.brand`).
Writes require `expected_version` and `request_id`; update operations identify
the library namespace and a newer source version. Use `get node="$libraries"`
with its returned cursor to read provenance that exceeds a normal text page.

### Starter templates

Create a document from a desk-buddy, phone, desktop or game HUD starter on the
UI Designer list page. The picker shows canvas dimensions and a short description.
Starters include editable content, tokens and reusable components; they become
ordinary independent documents after creation.

Agents can discover the catalog with `templates`, then create with a `template`
object containing its `id` and `revision`, plus the usual slug and request ID.
An optional title overrides the starter title. Retry uncertain creates with the
same request ID and inputs. Templates need no external assets or font downloads.

### Text controls

The text inspector supports horizontal alignment including justification, vertical
alignment within a fixed text box, underline/line-through decoration and text case.
These are ordinary versioned properties: `align`, `vertical_align`, `decoration`
and `text_case`. Choose Default to remove an explicit setting.

Case changes affect appearance while preserving authored text and styled runs.
Justification distributes space between words on non-final paragraph lines; it
does not stretch single words or ellipsis lines. Vertical alignment uses available
space in a definite text box. HTML exports use an SVG fallback for these settings
to preserve the renderer's typography.

### Grid, bounds and resizing

Select a frame and choose **Grid** under Direction to arrange children in equal
columns. Set Grid columns (1–12), Row gap, Column gap and Padding; empty row/column
gaps inherit Gap. Row and Column layouts also expose Wrap. Absolute children keep
their positions outside the grid.

Minimum/maximum width and height limit a node's rendered size while preserving
its authored width/height. Resize constraints control how free-positioned or
absolute children respond to a parent's resize: Start, End, Center, Stretch or
Scale on each axis. Active constraints require numeric parent and child geometry;
Scale also requires a positive original parent size. Geometry uses the local
unrotated box, then rendering applies rotation.

Inspector dimension changes and canvas resizing use an atomic `resize` operation.
Agents can use `{op:"resize", id:"frame", set:{w:480}}` as the only operation in
an edit batch. It returns exact `undo_ops`, including dependent child geometry;
apply those literal operations to undo. A normal `update` changes only the named
fields. The same request ID replays the saved result, including its undo data.

### Paths and Boolean operations

Use **New path** to draw in the selected frame or current page. Click to add line
or cubic segments, drag anchors/control handles, or edit their coordinates. Arrow
keys move a focused handle by one unit; Shift moves it by ten. **Save path** makes
one undoable edit. Existing path placement and rotation stay intact when editing
its points. The visual editor supports M/L/C/Z; other SVG commands remain in the
raw data editor until you explicitly reset the draft.

Select 2–8 sibling rectangles, ellipses or paths in order, then apply Union,
Subtract, Intersect or Exclude. The first shape keeps its ID and style. Subtract
cuts the later shapes out of the first. These operations combine filled contours;
they do not expand strokes. Shapes must have fixed numeric geometry and be outside
auto-layout flow (or all absolute). Geometry is baked into one native path.

Agents use one edit operation, `{op:"boolean",kind:"subtract",ids:["base","cut"]}`,
with an expected version and request ID. Apply the returned literal `undo_ops` to
restore the original shapes; redo the original request with a new version and ID.
Saved request IDs replay their original result without recomputing geometry.

Paths can use images already placed in the document. **Load placed images** shows
the available choices; **Apply image fill** replaces the path's fill paints while
preserving other properties. The image is clipped to the path. Image placement
permissions and the existing asset budgets still apply.

### Built-in icons

Open **Icons**, search the bundled catalog and choose an icon to preview it. Set
its size, colour and stroke width, then insert it into the selected frame or
current page. Icons are editable native components: their vector coordinates and
stroke scale to the chosen size, and identical definitions can be reused.
Locally edited definitions are protected from accidental replacement.

Agents can discover the catalog through the `icons` action, then use one edit
operation `{op:"icon",icon:"camera",revision:1,parent:"frame",id:"camera-instance",
size:24,color:"#64748b",stroke_width:2}` with an expected version and request ID.
Use the returned `undo_ops` for exact undo. The catalog is bundled from Lucide;
its source revision and license notices ship with the application.

**Live references and design sprints.** Relay `[[ui:my-design#node-id]]` references show a live thumbnail and open the selected node. Omit `#node-id` for the document. Previews load near the viewport, share one event stream and show an unavailable state when access or rendering fails. Rapid-chats can create a new UI Designer document from validated native frame proposals; only the controller writes it, and a concurrent human edit stops those writes. See [Rapid-chat](rapid-chat.md).
