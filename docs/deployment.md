# How changes go live

This is the agent-platform repository (`github.com/kylep/agent-platform`).
Two different things ship two different ways. No Argo, no CD pipeline (yet) —
the mechanism is deliberately boring.

The commands below are written against the reference deployment: a single-node
k3s cluster on a host named `pai`, Helm release `ap`, namespace
`agent-platform`. Substitute your own host and release name; the shapes hold.
Component names (dispatcher, runner, broker…) are defined in
`docs/building-blocks/glossary.md`.

## Configuration — automatic, via git-pull

Everything the platform treats as configuration — `agents/`, `skills/`,
`secrets/` (shapes, never values), `tools/`, `reports/`, and the
`docs/building-blocks/` help pages — is **live data**, not code. The
`agents-sync` Deployment clones this repository and, on a loop, hard-resets a
shared volume to `origin/main`:

```
git fetch origin main && git checkout main && git reset --hard origin/main
sleep $SYNC_INTERVAL_SECONDS
```

Every other service reads from that volume — the **synced checkout**. So the
self-hosting loop closes on its own: **edit a definition → PR → merge to
`main` → within one sync interval it is live**. Nothing else to do; no
redeploy.

`agents-sync` tracks the branch in `agents.gitRef` (default `main`; it clones
the public repo over HTTPS, no credential needed).

## Platform code — manual, image by image

Images and the Helm chart are deployed by hand. Build for the cluster's
architecture (`linux/amd64` on the NUC), save the image over SSH into k3s's
containerd namespace, then restart whatever runs it:

```sh
docker buildx build --platform linux/amd64 --load -t <image>:dev <context>
docker save <image>:dev | ssh pai 'sudo k3s ctr -n k8s.io images import -'
kubectl -n agent-platform rollout restart deploy/<deployment>
```

`-n k8s.io` is not optional: images imported into any other containerd
namespace are invisible to kubelet.

| Image | Build context | Runs as |
|---|---|---|
| `agent-platform-backend` | `services/backend` | `deploy/ap-api`, `deploy/ap-dispatcher`, `deploy/ap-recorder` — **one image, three deployments**; restart all three |
| `agent-platform-runner` | `services/runner` | no deployment — the dispatcher launches it as a Job per run, so a new image applies to the *next* run with no restart |
| `agent-platform-web` | `services/web`, using `Dockerfile.prebuilt` after `npm run build -w web` | `deploy/ap-web` |
| `agent-platform-mcp-broker` | `services/mcp-broker` | `deploy/ap-mcp-broker` |
| `agent-platform-tool-executor` | **the repository root** (it bakes the union of `tools/*/requirements.txt`) | `deploy/ap-tool-executor` |
| `agent-platform-connector-discord` | `services/connector-discord` | `deploy/ap-connector-discord` |
| `agent-platform-app-news` | `apps/news` | `deploy/ap-app-news` |

Two more deployments run stock upstream images and are never built here:
`ap-agents-sync` (`alpine/git`) and `ap-claude-proxy` (nginx plus a config from
the chart).

## Chart changes

Template or values changes go out with Helm. Always re-supply the current
values rather than trusting `--reuse-values`, which silently drops values that
new templates need:

```sh
helm get values ap -n agent-platform > /tmp/ap-values.yaml
helm upgrade ap charts/agent-platform -n agent-platform -f /tmp/ap-values.yaml
```

## What has no automation yet

There is no image CD or GitOps for the platform itself — that's a deliberate
later step (a milestone could add Argo/Flux plus an image updater). Until then,
code changes are a human (or an agent) running the lines above;
configuration changes need none of it.
