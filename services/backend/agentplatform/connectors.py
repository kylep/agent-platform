"""Connector registry. A connector bridges an external channel (or the web UI)
to conversations via Kafka (conversation.inbound / conversation.outbound).

This is a static list of known connectors; `implemented=false` ones (Slack) are
surfaced in the UI as "Not Yet Implemented" placeholders."""

CONNECTORS = [
    {"name": "web", "kind": "web", "implemented": True,
     "description": "Create and continue conversations from the platform UI."},
    {"name": "discord", "kind": "discord", "implemented": True,
     "description": "Discord bot — mention it or reply in its thread to talk to an agent."},
    {"name": "slack", "kind": "slack", "implemented": False,
     "description": "Slack app — not yet implemented."},
]

KNOWN = {c["name"] for c in CONNECTORS}
IMPLEMENTED = {c["name"] for c in CONNECTORS if c["implemented"]}
