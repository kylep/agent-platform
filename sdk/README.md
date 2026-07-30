# agent-platform Python SDK

A **typed** client for the agent-platform HTTP API, **generated from the
platform's OpenAPI spec** with [openapi-python-client]. The generated code lives
in `agent_platform_sdk/` and is never hand-edited — the spec is the single
source of truth, so the client can't drift from the API.

## Install

```bash
pip install ./sdk
```

## Use

Authentication is one `ap_` API key (mint one in the platform UI under
Settings → API keys). The key's role decides what it can do — you need an
`operator`+ key to trigger runs. Each endpoint is a module under
`agent_platform_sdk.api.default`; call `.sync(client=…)` for the parsed result
or `.sync_detailed(client=…)` for the full response.

```python
from agent_platform_sdk import AuthenticatedClient
from agent_platform_sdk.api.default import list_agents, create_run, get_run
from agent_platform_sdk.models import RunIn

client = AuthenticatedClient(base_url="http://pai:8090", token="ap_your_key_here")

# List agents (typed: list[AgentSummary])
for a in list_agents.sync(client=client):
    print(a.name, a.description)

# Trigger a run and poll it
run = create_run.sync(client=client, body=RunIn(agent="echo", prompt="hello"))
print(get_run.sync(client=client, run_id=run.id).state)
```

## Regenerate

After any API change, regenerate and commit:

```bash
python sdk/regenerate.py
```

CI runs this and fails if the committed `sdk/` differs from what the current
OpenAPI produces (`git diff --exit-code sdk/`), so the SDK stays in lockstep
with the API by construction.

[openapi-python-client]: https://github.com/openapi-generators/openapi-python-client
