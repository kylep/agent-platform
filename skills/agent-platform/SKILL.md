---
name: agent-platform
description: Operate the agent-platform (list/inspect agents, trigger and watch runs, browse memory) from any Claude session using a single ap_ API key. Use when the user wants to run a platform agent, check run status, or manage platform resources over its HTTP API.
---
# Operating the agent-platform

This skill lets a Claude session drive an agent-platform install over its HTTP
API. Everything is one authenticated REST surface; you need two things:

- `AP_API_URL` — the platform base URL (e.g. `http://pai:8090`).
- `AP_API_TOKEN` — an `ap_` API key (mint one in the UI under **Settings → API
  keys**). The key's **role** bounds what you can do: `reader` sees runs,
  `operator`+ can trigger runs, `admin` can do everything.

If the user hasn't provided these, ask for the URL and key (never guess a key).

Prefer the Python SDK when available (`sdk/` in this repo), else use `curl`.
Always send `-H "Authorization: Bearer $AP_API_TOKEN"`.

## Common operations (curl)

List agents:
```bash
curl -s -H "Authorization: Bearer $AP_API_TOKEN" "$AP_API_URL/api/agents"
```

Inspect one agent (manifest + definition):
```bash
curl -s -H "Authorization: Bearer $AP_API_TOKEN" "$AP_API_URL/api/agents/<name>"
```

Trigger a run (needs an operator+ key), then poll it:
```bash
curl -s -X POST -H "Authorization: Bearer $AP_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"agent":"echo","prompt":"hello"}' "$AP_API_URL/api/runs"
curl -s -H "Authorization: Bearer $AP_API_TOKEN" "$AP_API_URL/api/runs/<id>"
```
A run's `state` moves queued → dispatched → running → succeeded|failed. Poll
`/api/runs/<id>` until the state is terminal.

Recent runs (with summaries/tags):
```bash
curl -s -H "Authorization: Bearer $AP_API_TOKEN" "$AP_API_URL/api/runs?limit=20"
```

Platform health (broker liveness + backlog):
```bash
curl -s -H "Authorization: Bearer $AP_API_TOKEN" "$AP_API_URL/api/health/kafka"
```

## Using the Python SDK

```python
from agent_platform_sdk import Client
ap = Client("$AP_API_URL", "$AP_API_TOKEN")
ap.list_agents()
run = ap.create_run("echo", "hello")
ap.get_run(run["id"])
```

## Notes

- Report a run by linking `"$AP_API_URL/runs/<id>"` so the user can open it.
- 401 = bad/missing key; 403 = the key's role isn't allowed that action.
- The full request/response schema is the live OpenAPI at `$AP_API_URL/openapi.json`.
