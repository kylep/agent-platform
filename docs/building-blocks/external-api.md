# External API access

The platform's HTTP API is available to clients outside a run. Set
`AP_API_URL` to the platform base URL and create an `ap_` API key in
**Settings → API keys**. The key's role bounds access: `reader` reads,
`operator` can trigger runs, and `admin` can manage the platform. Send
`Authorization: Bearer $AP_API_TOKEN` on each request. Inside a platform run,
use the run-scoped platform tools instead of an external key.

```bash
curl -sS -H "Authorization: Bearer $AP_API_TOKEN" "$AP_API_URL/api/agents"
curl -sS -X POST -H "Authorization: Bearer $AP_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"agent":"pai","prompt":"hello"}' "$AP_API_URL/api/runs"
curl -sS -H "Authorization: Bearer $AP_API_TOKEN" "$AP_API_URL/api/runs/<id>"
```

A run moves from queued through dispatched and running to succeeded or failed.
`GET /api/runs?limit=20` lists recent runs; `/runs/<id>` is the browser view.
`GET /api/health/kafka` reports broker and backlog health. A 401 means the
key is missing or invalid; a 403 means it lacks permission. The live endpoint
schema is at `/openapi.json`.

The generated Python client lives under `sdk/`. For example:

```python
import os
from agent_platform_sdk import AuthenticatedClient
from agent_platform_sdk.api.default import list_agents

client = AuthenticatedClient(
    base_url=os.environ["AP_API_URL"], token=os.environ["AP_API_TOKEN"])
agents = list_agents.sync(client=client)
```
