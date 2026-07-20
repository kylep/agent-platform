# How changes go live

Two different things ship two different ways. No Argo, no CD pipeline (yet) —
the mechanism is deliberately boring.

## Agent definitions — automatic, via git-pull

Everything under `agents/` (agent prompts + manifests) is **live data**, not
code. An `agents-sync` sidecar clones the repo and, on a loop, hard-resets a
shared volume to `origin/main`:

```
git fetch origin main && git checkout main && git reset --hard origin/main
sleep $SYNC_INTERVAL_SECONDS
```

The dispatcher and every runner pod read agent definitions from that volume.
So the self-hosting loop closes on its own: **edit an agent → PR → merge to
`main` → within one sync interval the change is live**, and the dispatcher
re-reads agents on each run. Nothing else to do; no redeploy.

`agents-sync` tracks the branch in `agents.gitRef` (default `main`; it clones
the public repo over HTTPS, no credential needed).

## Platform code — manual, image by image

The backend / runner / web **images** and the Helm chart are deployed by hand:

```
docker buildx build --platform linux/amd64 -t agent-platform-<svc>:dev services/<svc>
docker save agent-platform-<svc>:dev | ssh pai 'sudo k3s ctr images import -'
kubectl -n agent-platform rollout restart deploy/ap-<svc>
```

There is no image CD or GitOps for the platform itself yet — that's a
deliberate later step (a milestone could add Argo/Flux + an image updater).
Until then, code changes are a human (or this agent) running the three lines
above; agent-definition changes need none of it.
