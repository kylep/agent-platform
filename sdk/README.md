# agent-platform Python SDK

A tiny, dependency-free client for the agent-platform HTTP API. It mirrors the
platform's OpenAPI (served live at `/openapi.json`) with a hand-written client
so it runs anywhere with only the Python standard library.

## Install

It's a single package; copy `agent_platform_sdk/` onto your path, or install
this directory:

```bash
pip install ./sdk
```

## Use

Authentication is one `ap_` API key (mint one in the platform UI under
Settings → API keys). The key's role decides what it can do — you need an
`operator`+ key to trigger runs.

```python
from agent_platform_sdk import Client

ap = Client("http://pai:8090", "ap_your_key_here")

# List agents
for a in ap.list_agents():
    print(a["name"], a["description"])

# Trigger a run and poll it
run = ap.create_run("echo", "hello from the SDK")
print(ap.get_run(run["id"])["state"])
```

The HTTP transport is injectable (`Client(..., fetch=...)`) for testing.
