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

Everything the platform treats as *capability* — `skills/`, `secrets/`
(shapes, never values), `tools/`, `reports/`, and the
`docs/building-blocks/` help pages — is **live data**, not code. The
`agents-sync` Deployment clones this repository and, on a loop, hard-resets a
shared volume to `origin/main`:

```
git fetch origin main && git checkout main && git reset --hard origin/main
sleep $SYNC_INTERVAL_SECONDS
```

Every other service reads from that volume — the **synced checkout**. So the
self-hosting loop closes on its own for capability: **edit a skill/tool/secret
→ PR → merge to `main` → within one sync interval it is live**. Nothing else
to do; no redeploy.

`agents-sync` tracks the branch in `agents.gitRef` (default `main`; it clones
the public repo over HTTPS, no credential needed).

**Agent definitions are the one exception.** Since
[docs/design/15-db-first-agents.md](design/15-db-first-agents.md), an agent's
prompt/grants/entrypoints/config is a Postgres row (`agent_defs`), not a file
`agents-sync` pulls — edits through the UI/API or the `agents_edit`/
`agents_grant` platform tools apply immediately, with an append-only
`agent_versions` log standing in for the PR record. `agents-sync` no longer
serves agent definitions at all; it still serves everything else in the list
above. The migration itself was a one-time admin import (`POST
/api/agents/import`) that seeded `agent_defs` from the final state of the
`agents/` tree; the tree is deleted from the repo after that live import
succeeds — if you are reading this before it has, `agents/` may still be
present but is no longer read by anything.

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
| `agent-platform-app-stockmarket` | `apps/stockmarket` | `deploy/ap-app-stockmarket` |

App images build from the **repository root** with `-f apps/<name>/Dockerfile .`
and expect the frontend prebuilt on the host first
(`npm run build -w <name>-frontend`) — there is no node toolchain in the image.

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

## Postgres backups

With agent identity fully in the database ([design-15](design/15-db-first-agents.md)),
Postgres is the recovery story for more than history — losing it now loses
every agent's prompt/grants/entrypoints too, not just runs and transcripts. A
`pg-backup` CronJob (`charts/agent-platform/templates/pg-backup.yaml`) dumps
the whole database daily:

- **Schedule/retention** — `backup.schedule` (default `"0 8 * * *"`, 08:00
  UTC), `backup.keep` (default 14 dumps), `backup.enabled` (default `true`),
  `backup.storage` (PVC `ap-pg-backups`, default 5Gi), `backup.storageClass`.
  All values-driven; `helm template … --set backup.enabled=false` renders
  neither the PVC nor the CronJob.
- **What it does** — `pg_dump | gzip` to `/backups/ap-<UTC timestamp>.sql.gz`
  via an atomic temp-file rename (a failed/partial dump is never counted or
  left under its final name), then deletes everything on the PVC beyond the
  newest `backup.keep` dumps. `concurrencyPolicy: Forbid` so a slow dump can't
  overlap the next tick.
- **Image** — pinned to the exact `bitnami/postgresql` image reference the
  chart's `postgresql` subchart itself resolves to by default, so `pg_dump`
  is always the same binary the server runs. **That tag is floating
  (`:latest`)** — the same posture the subchart's own image already has today
  (no `postgresql.image` override exists in `values.yaml`); pinning either one
  to a specific version is a future values change, not something this CronJob
  can do alone, and the two need to move in lockstep if it happens.
- **Network path** — the backup pod is not named in the chart's own
  `allow-postgres` NetworkPolicy component allowlist. It reaches Postgres
  anyway because the bitnami `postgresql` subchart renders its **own**
  NetworkPolicy on the `ap-postgresql` pod, and its default
  `primary.networkPolicy.allowExternal: true` means that policy's ingress rule
  carries no `from:` selector at all — it permits ingress from ANY source, not
  just a pod in this namespace. This is a low-margin coincidence, not a
  designed grant: if `postgresql.networkPolicy`
  is ever disabled, or if `allow-postgres` is tightened to a `from:`-scoped
  rule that doesn't list the backup pod, the CronJob will silently start
  failing on network denial rather than a visible config error.
- **Restore is manual** — there is no restore script or drill in the chart.
  Recovering means `gunzip` a dump and `psql` it back into a fresh
  `ap-postgresql`, by hand, using the same `PGHOST`/`PGUSER`/`PGDATABASE`/
  `PGPASSWORD` wiring the CronJob uses (see the chart's `pg-backup.yaml` for
  the exact env). Restoring loses everything written after the dump's
  timestamp, including any `agent_versions` rows since — expected for a daily
  backup, worth knowing before relying on it as the sole recovery path.

## What has no automation yet

There is no image CD or GitOps for the platform itself — that's a deliberate
later step (a milestone could add Argo/Flux plus an image updater). Until then,
code changes are a human (or an agent) running the lines above;
configuration changes need none of it.
