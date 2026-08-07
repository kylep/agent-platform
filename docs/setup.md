# Agent Platform Setup

How to get an agent-platform install running on a Kubernetes cluster from
nothing. Concepts referenced here (dispatcher, runner, synced checkout…) are
defined in `docs/building-blocks/glossary.md`.

**The reference deployment.** These instructions were written against the one
install that exists: a single-node k3s cluster on a home Intel NUC named `pai`,
Helm release `ap`, namespace `agent-platform`, UI on `http://pai:8090`. Where a
command mentions `pai` or `values-pai-nuc.yaml`, that's the reference — a
different cluster needs its own host name and its own values file. Nothing in
the chart is NUC-specific except the values.

## Prerequisites

- **Helm 3.0+** and **kubectl**, with kubectl pointed at the target cluster
  (`kubectl cluster-info` should answer). If you keep several clusters, set
  `KUBECONFIG` explicitly for every command below rather than trusting the
  current context — mixing up a local cluster (Rancher Desktop, kind) with the
  real one is the classic mistake here.
- A cluster with a **default StorageClass** (Postgres and Kafka both want
  volumes); k3s ships the local-path provisioner.
- A **Claude Pro/Max subscription** — agents run Claude Code, so the platform
  needs a credential to run anything.
- The repository itself, since the chart and the agent definitions live in it:

```bash
git clone https://github.com/kylep/agent-platform.git
cd agent-platform
```

## Installation

### 1. Install the Helm chart

```bash
helm dependency update charts/agent-platform
helm install ap charts/agent-platform \
  -f charts/agent-platform/values-pai-nuc.yaml \
  --set env.AP_SESSION_SECRET=$(openssl rand -hex 32) \
  -n agent-platform \
  --create-namespace
```

This will:
- Create the `agent-platform` namespace
- Deploy PostgreSQL and Kafka from bitnami Helm charts
- Create Kafka topics via the topics job
- Start the platform services, including `agents-sync`, which clones this
  repository into a shared volume so the platform can read the agent, skill,
  and tool definitions in `agents/`, `skills/`, and `tools/`

### 2. Verify the deployment

Watch the rollout:

```bash
kubectl rollout status deployment/ap-api -n agent-platform
kubectl rollout status deployment/ap-dispatcher -n agent-platform
```

All pods should reach `Running` state.

### 3. Reach the UI

The web service is a `LoadBalancer` on port **8090**, forwarding to the UI's
8080 (`web.service` in `charts/agent-platform/values.yaml`). On k3s, the
built-in ServiceLB publishes that on the node itself, which is why the
reference install is reachable at `http://pai:8090`. Use your own node's name
or address; with no load balancer at all, `kubectl -n agent-platform port-forward
svc/ap-web 8090:8090` works just as well.

## First Launch

### 1. Create admin credentials

Open the UI. The platform has no users yet, so it shows a first-launch setup
page: choose the admin email and password there. Until that's done — and after
that, until you log in — every page is gated.

### 2. Set Claude credentials

Next you'll hit a secrets gate asking for Claude subscription credentials.
Nothing can run without them.

#### Option A (recommended): setup-token via UI

Run `claude setup-token` in any terminal (requires a Claude Pro/Max
subscription; walks you through a browser OAuth authorization and prints a
long-lived token valid for one year). Copy the token and paste it into the
Secrets page. Nothing rotates this token, so it does not go stale the way a
copied session credentials file does — a session file's refresh token is
invalidated as soon as the machine that exported it refreshes again.

#### Option B: Use the install script

`bin/set-claude-token.sh` reads local Claude Code credentials and writes them
into the cluster as the `claude-credentials` secret. It takes the kubectl
command to use and the target namespace:

```bash
KUBECONFIG=~/.kube/pai-nuc.yaml bin/set-claude-token.sh kubectl agent-platform
```

It reads `~/.claude/.credentials.json` by default. On macOS, Claude Code stores
those credentials in the Keychain rather than a file, so export them first and
point the script at the export:

```bash
security find-generic-password -s "Claude Code-credentials" -w > /tmp/claude-creds.json
KUBECONFIG=~/.kube/pai-nuc.yaml CLAUDE_CREDENTIALS_FILE=/tmp/claude-creds.json \
  bin/set-claude-token.sh kubectl agent-platform
rm /tmp/claude-creds.json
```

After running, refresh the browser to clear the gate.

## Smoke Test

Once the secrets gate is satisfied, run any agent once — `pai`, the
conversational assistant, is the simplest:

1. In the UI, navigate to **Agents**
2. Click **pai**
3. In the prompt field, enter: `Say OK and nothing else`
4. Click **Run now**
5. Watch the live transcript as the run starts, the agent replies, and the run
   finishes as `succeeded`

That confirms the whole loop: UI → API → Kafka → dispatcher → a Kubernetes Job
running the agent → transcript events back through Kafka → recorder → the live
feed. If the run is instead `rejected`, the message says why (usually a secret
the agent's skills require); see `docs/building-blocks/runs.md`.

## Teardown

To uninstall agent-platform from the cluster:

```bash
helm uninstall ap -n agent-platform
```

This removes all resources (deployments, statefulsets, services, secrets, PVCs) but preserves the namespace. To remove the namespace as well:

```bash
kubectl delete namespace agent-platform
```

## Troubleshooting

### Pods stuck in Pending

**Symptom:** Pods remain in `Pending` state after install.

**Cause:** Missing PersistentVolumeClaim or StorageClass.

**Fix:** Verify your cluster has a default StorageClass:

```bash
kubectl get storageclass
```

If missing, install one appropriate for your cluster (e.g., local-path provisioner for k3s).

### Secrets gate stuck

**Symptom:** The UI secrets gate is not dismissing after pasting credentials or running the script.

**Cause:** Secret not created successfully, or the API is not probing it correctly.

**Fix:**

1. Verify the secret exists:
   ```bash
   kubectl get secret claude-credentials -n agent-platform
   ```

2. If missing, run the install script again:
   ```bash
   bin/set-claude-token.sh kubectl agent-platform
   ```

3. Check the API pod logs for gate probe errors:
   ```bash
   kubectl logs -f deployment/ap-api -n agent-platform
   ```

### Runs stuck in Queued state

**Symptom:** Agent runs show as `Queued` but never transition to `Running`.

**Cause:** The dispatcher is not consuming run commands, or the Kafka topics
were never created.

**Fix:**

1. Check the dispatcher logs:
   ```bash
   kubectl logs -f deployment/ap-dispatcher -n agent-platform
   ```

2. Verify Kafka topics were created:
   ```bash
   kubectl exec -it ap-kafka-controller-0 -n agent-platform -- \
     kafka-topics.sh --list --bootstrap-server localhost:9092
   ```

   You should see the run topics (`run.inbound`, `run.requests`, `run.events`,
   `run.transcript`, `run.dlq`) among the others the chart declares in
   `topics.specs`.

3. If topics are missing, the topics job may have failed. Re-run the Helm install to trigger a fresh job.

### Reset the admin password

The UI can change the password under **Settings** when you know the current
one. If it's lost, delete the admin principal and reload the UI — the
first-launch setup page returns and lets you choose a new one. The password
exists only as an argon2 hash in Postgres, so there is nothing to recover:

```bash
kubectl -n agent-platform exec ap-postgresql-0 -- \
  env PGPASSWORD=$(kubectl -n agent-platform get secret ap-postgresql \
    -o jsonpath='{.data.postgres-password}' | base64 -d) \
  psql -U postgres -d agentplatform -c "DELETE FROM principals WHERE name='admin';"
```

Existing browser sessions are invalidated the next time they hit an
authenticated endpoint.
