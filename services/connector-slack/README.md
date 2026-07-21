# Slack connector — Not Yet Implemented

Slack is registered in the connector registry (`agentplatform/connectors.py`)
with `implemented: false`, so it appears in the UI as a greyed "NYI" chip. No
service is deployed.

## To implement later

A Slack connector is the same shape as the Discord one
(`services/connector-discord/`): a thin long-lived bridge that owns no
conversation state.

**Inbound** — on an app_mention (or a message in a thread the bot is active in),
produce a `conversation.message` envelope to `conversation.inbound`:

```json
{ "type": "conversation.message", "schema_version": 1, "id": "…", "ts": "…",
  "key": "<slack thread ts>", "source": "connector:slack",
  "data": { "connector": "slack", "external_ref": "<channel>:<thread_ts>",
            "external_user": "<user>", "text": "<message>", "agent": "<default>" } }
```

**Outbound** — consume `conversation.outbound`, filter `data.connector == "slack"`,
and `chat.postMessage` the `data.text` to the channel/thread named by
`data.external_ref`.

That's the whole contract — the platform owns the conversation, history, and the
agent run. Flip `implemented: true` in the registry, add
`services/connector-slack/` (Socket Mode client + the two Kafka bridges), a
`connectors.slack` block in `values.yaml`, and a gated Deployment template.
