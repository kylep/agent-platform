# Chat identities

A Chat Identity names an external account the platform speaks through. It is
separate from an MCP Tool: the Discord bridge receives conversations and
mirrors Relay rooms, while `discord_chat` is a callable Tool that posts a
notification. Both currently use the same `discord-bot` secret.

The existing bot is `discord-default`. Its database row stores its network,
display name and credential **references** (`discord-bot`, key `token`), never
the token bytes. Settings shows whether that secret exists and how many Relay
routes are bound to the identity. Secret rotation still happens through the
existing Secrets flow and does not rename the identity.

Every existing Discord Relay binding is attached to `discord-default` by an
idempotent migration; new Discord bindings use it automatically. The bridge
includes that ID on inbound events and ignores recovered bindings and outbound
events for another ID. This preserves the current single-bot route while
making attribution explicit. A message still belongs to a Relay room and its
membership rules; an identity is not a blanket grant to read that room.

This release does not enable a second Discord account or a status toggle. That
requires a per-identity connector credential, identity-scoped destination
selection for `discord_chat` and platform broadcasts, and tests for rotation,
revocation and duplicate route resolution. The existing Tool now rejects a
channel name shared by multiple servers; pass an exact channel ID in that
case. See [the design](../design/33-capabilities-plugins-and-live-apps.md).
