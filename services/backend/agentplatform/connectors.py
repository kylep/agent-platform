"""Connector registry. A connector bridges an external channel (or the web UI)
to conversations via Kafka (conversation.inbound / conversation.outbound).

This is a static list of known connectors; `implemented=false` ones (Slack) are
surfaced in the UI as "Not Yet Implemented" placeholders."""

CONNECTORS = [
    {"name": "web", "kind": "web", "implemented": True, "secrets": [],
     "description": "Create and continue conversations from the platform UI."},
    {"name": "discord", "kind": "discord", "implemented": True, "secrets": ["discord-bot"],
     "description": "Discord bot — mention it or reply in its thread to talk to an agent."},
    {"name": "slack", "kind": "slack", "implemented": False, "secrets": [],
     "description": "Slack app — not yet implemented."},
]

# Secrets any implemented connector needs (surfaced on the Secrets page).
CONNECTOR_SECRETS = sorted({s for c in CONNECTORS if c["implemented"] for s in c.get("secrets", [])})

KNOWN = {c["name"] for c in CONNECTORS}
IMPLEMENTED = {c["name"] for c in CONNECTORS if c["implemented"]}
