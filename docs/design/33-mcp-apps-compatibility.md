# MCP Apps export gate for database Apps

Checked 2026-09-25 against the platform's current clients and the official
[MCP Apps overview](https://apps.extensions.modelcontextprotocol.io/api/documents/overview.html)
and [OpenAI plugin UI guide](https://developers.openai.com/plugins/build/chatgpt-ui).
This is a compatibility decision, not a claim that the platform implements
MCP Apps today.

The platform's first-party Live View is a browser page with a verified human
session. Private reads are rendered by trusted React components. A write
requires a host-created intent, explicit operation grant, target recheck and
idempotent receipt. The facade currently exposes ordinary MCP Tools and
authenticated `ap://` Resources; it does not expose a `ui://` resource or
`_meta.ui.resourceUri` Tool metadata.

MCP Apps associates a Tool with a `ui://` HTML Resource and lets the host
render it in an iframe. The UI can call Tools through the host. OpenAI's
current guide describes the optional component as rendered in ChatGPT and
asks servers to keep Tools useful without a component for clients that do not
render one. No pinned platform Claude Code/Codex runner image or local CLI has
been verified as an MCP Apps host. Tool visibility metadata is a menu hint,
not the platform's object ACL or human intent.

Therefore this release does not export private Live Views or their actions as
MCP Apps. A generic `ui://` wrapper would either hand private data to an
unreviewed script/host path or lose the trusted confirmation boundary. The
existing facade's opaque platform keys also are not an OAuth authorization
flow for third-party hosts. Ordinary Tools and `ap://` Resources remain the
portable, non-UI interface.

An export adapter can be admitted when one concrete host passes this matrix:

1. Pin the host and MCP Apps extension revision; prove `ui://` fetch and
   `_meta.ui.resourceUri` rendering on that exact binary.
2. Trace what Tool results enter the model context and what reaches only the
   UI. Never put private view rows in model-visible `content` merely to drive
   the component.
3. Authenticate the person and enforce the App/View, operation, object and
   target policy server-side on every UI Tool call. Prove revocation.
4. Make the host's affirmative action bind the exact arguments and target to
   the platform intent; a UI script cannot assert a click by itself.
5. Test teardown/navigation, cached Resources, mobile layout, denial and
   rollback. Keep the ordinary Tool result useful when no UI renders.

Until those checks pass, the first-party Apps directory is the supported
interactive surface. This is a narrowly deferred export format, not an
unmigrated domain App.
