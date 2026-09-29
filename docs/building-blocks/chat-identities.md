# Chat identities

A Chat Identity is one external chat account. Its row stores a name, network,
and a reference to a Secret; bot tokens stay in the existing secret store.
Relay bindings connect exact external rooms to an identity. An identity is
separate from the `discord_chat` Tool: the bridge receives and mirrors
conversations, while the Tool sends direct notifications.

## Managing accounts

Settings → Connections → Discord chat identities shows the account inventory.
Choose **Add account** to enter a display name and masked bot token. The
platform generates an internal ID from the name, such as `discord-family`,
and stores its credential separately in `discord-family-bot`. This is not a
Discord application ID.

Each account has three actions:

| Action | What it does |
| --- | --- |
| Edit | Rename the account or replace its token. Leave the token blank to keep it. Room routes and agent assignments stay attached to the stable internal ID. A token replacement restarts only that bot's deployed connector. |
| Verify | Make one read-only Discord request to check the token and Message Content Intent, then inspect the connector Deployment. No test message is sent. Missing credentials, rejected tokens, missing intent, and undeployed processes get explicit results. A transient provider error leaves the existing transport state alone. |
| Delete | After confirmation, disconnect its routes and agent assignments, remove its credential, and scale its connector to zero. Chat history stays. Failed cleanup leaves an account visible with a retry instruction. |

There is no Pause/Resume switch. The internal transport guard remains:
configured accounts can operate, failed credential/intent checks disable
transport, and a successful Verify restores it. Deployment readiness means
that the process is running; it does **not** prove the Discord gateway is
connected or that the bot can send to every destination. Verify reports these
limits rather than claiming delivery was tested.

## Deploying another bot

Saving an account registers its credential and metadata; it does not provision
a new bot process. Configure `connectors.discord.extraIdentities` in Helm with
that account's ID and `secretName`, then deploy the chart. Each connector gets
only its own token and a separate projected Kubernetes service-account
identity. Each has its own Kafka consumer group and delivers only its own
messages. The original bot retains its existing Kafka offsets.

For a private persona, disable **Public Bot** under Bot in the Discord
Developer Portal before starting its connector. With Public Bot on, someone
with the install link and permission to install apps could add it to another
server; this platform currently accepts mentions and direct messages from
any accessible endpoint. Set `replicas: 0` on the extra identity to keep its
workload disconnected while finishing setup, then set it to `1` and upgrade
Helm when ready. Verify checks token, intent, and deployment readiness; it
does not restrict which Discord users may talk to a connected bot.

Enable **Message Content Intent** under Bot in the
[Discord Developer Portal](https://discord.com/developers/applications).
Use Verify to check the account. Bind Relay rooms through
`POST /api/relay/channels/{channel_id}/bindings`, specifying `identity_id` and
an exact Discord room ID. Select an agent's outbound identity in Grants.

The Helm `secretName` must match the account's Secret reference. When deleting
an extra account, remove its `extraIdentities` entry on the next chart edit.
A future Helm deployment cannot restore its deleted token or activate its
retained tombstone, even if it recreates the connector workload. The default
account also retains a tombstone so database initialization cannot recreate it.
Deleted internal IDs cannot be reused; adding a replacement creates a new ID.
A provider send already in flight cannot be recalled.

## Permissions and routing

`discord-default` names the original bot, using `discord-bot`. Legacy messages
and bindings without an identity belong only to it. New agents select none
until an admin assigns an outbound identity. Both the `discord_chat` Tool
grant and the selected identity are required to send. Relay room membership
still controls access; selecting an external account does not grant read
access to a room.

Calls without `identity_id` name the default account and are rejected if the
agent selected another. Other identities require an exact `channel_id` bound
to that identity and the agent's membership in the bound Relay room. Sends
are queued through that identity's connector; queueing is not delivery.
`/api/notify` uses the same targeting rules for administrators.

The API checks status and credential presence for inbound traffic, and the
connector checks transport before outbound effects. External room IDs cannot
be claimed by two identities. Deleted bindings keep their original identity
and history, but stop routing; they never fall back to another bot. Account
management endpoints are admin-only and hidden from non-admin MCP clients.
Existing News, Stockmarket, and Running broadcasts use the default account
until explicitly migrated.
