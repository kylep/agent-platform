# Chat identities

A Chat Identity is one external chat account. The identity row stores its
name, network, status and a reference to a Secret; it never stores the bot
token. Relay bindings assign an exact external room to an identity. Pausing an
identity stops inbound and outbound bridge traffic while retaining its routes.
It is separate from an MCP Tool: the bridge receives conversations and mirrors
Relay rooms, while `discord_chat` is a callable Tool for a direct notification.
Both use the original `discord-bot` Secret for the default account. A Relay
room's membership still controls access; the external account is not a room
read grant.

`discord-default` is the existing Discord bot. Legacy messages and bindings
without an identity belong only to it. `discord_chat` and `/api/notify`
still use that bot when no identity is named. To send as another identity,
`discord_chat` requires `identity_id` and an exact `channel_id` already bound
to that identity. The platform checks that the sending agent has the Tool
grant and belongs to the bound Relay room. It queues the send through the
identity's connector and reports that queueing honestly; delivery is
asynchronous. Admins can use the same exact target through `/api/notify`.

To add another Discord bot:

1. Store its `token` in a separate platform Secret, such as
   `discord-second-bot`. Keep the original `discord-bot` Secret untouched.
2. In Settings, register an ID such as `discord-second`, a display name and
   that Secret name. The new identity starts paused; it cannot be resumed
   until the Secret contains a nonempty `token` key.
3. Configure `connectors.discord.extraIdentities` in Helm with the same ID
   and `secretName`, and deploy the chart. Each identity receives only its own
   Discord token. Its connector has a separate Kafka consumer group, so each
   bot sees the whole outbound stream and delivers only its own messages. Each
   extra connector uses its own projected Kubernetes service-account identity;
   the API returns only that bot's bindings and transport status to it. The
   original bot keeps its pre-migration Kafka consumer group and offsets.
4. Bind Relay rooms with `POST /api/relay/channels/{channel_id}/bindings`,
   supplying `identity_id` and the exact external room ID. Resume the identity
   in Settings after its connector is ready.

The API checks identity status and credential presence before accepting an
inbound Discord message; it rechecks the binding's identity before routing it.
The connector checks status before each outbound send. A binding's external
room ID is unique across Discord identities, so two bots cannot silently
claim the same Relay route. Removing a token or pausing the row stops new
platform sends; a provider request already in flight cannot be recalled.

The operator must keep the Helm `secretName` equal to the identity's Secret
reference. Registration does not launch a bot or prove that its external
account has joined a server. Until a second real bot credential is available,
the second-account path is tested with synthetic credentials and rendered
Kubernetes manifests rather than a live Discord send.

Rotate a bot token through the existing Secrets flow without changing its
identity or room bindings. The connector reads the Discord token when it
starts, so restart only that identity's connector after the Secret is synced;
its status check stops new sends if the token is removed or the identity is
paused. Resume the identity after the restarted connector is healthy. The
default `discord_chat` Tool and broadcast connector reject ambiguous channel
names; use an exact
channel ID when two visible rooms share a name. Existing domain broadcasts
remain on `discord-default` until their owners explicitly choose a bound
destination on another identity.
