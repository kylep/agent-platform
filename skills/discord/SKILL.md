---
name: discord
description: Post a message to a Discord channel via an incoming webhook. Use when an agent should notify a human channel — a run finished, something needs attention, or a summary is ready.
icon: 💬
secrets:
  - discord-webhook
---
# discord

Send a message to a Discord channel using an incoming webhook. The webhook URL
is a bound secret (`discord-webhook`); treat it as sensitive — anyone with it
can post to the channel.

## Post a message

The `discord-webhook` secret provides the webhook URL as `$DISCORD_WEBHOOK_URL`.
Post with a JSON `content` field (build the JSON with a file, never inline a
value that might contain quotes):

```bash
printf '%s' "$MESSAGE" > /tmp/msg.txt
# jq -Rs reads the whole file as a single JSON string, safely escaped
jq -Rs '{content: .}' /tmp/msg.txt > /tmp/payload.json
curl -s -X POST -H "Content-Type: application/json" \
  -d @/tmp/payload.json "$DISCORD_WEBHOOK_URL"
```

A 204 response means it posted. Keep messages short and link back to the
platform (`$AP_API_URL/runs/<id>`) rather than pasting long transcripts.

## Notes

- Discord truncates at 2000 characters; summarize rather than dumping output.
- Never echo `$DISCORD_WEBHOOK_URL` into transcript output — it is a credential.
- Rate limits apply (HTTP 429 with a `retry_after`); back off and retry once.
