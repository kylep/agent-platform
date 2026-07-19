# Agent Platform Setup

## Prerequisites

Before installing agent-platform, ensure you have:

- **Helm 3.0+** installed locally
- **kubectl** configured with context for the target cluster
- Verify with `kubectl cluster-info`

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
- Sync agent definitions from the `agents/` directory
- Start the API server and dispatcher

### 2. Verify the deployment

Watch the rollout:

```bash
kubectl rollout status deployment/ap-api -n agent-platform
kubectl rollout status deployment/ap-dispatcher -n agent-platform
```

All pods should reach `Running` state.

## First Launch

### 1. Create admin credentials

Navigate to **http://pai:8090** in your browser.

At the auth gate, create your admin credentials (email and password).

### 2. Set Claude credentials

You will see a secrets gate asking for Claude subscription credentials.

#### Option A (recommended): setup-token via UI

Run `claude setup-token` in any terminal (requires a Claude Pro/Max
subscription; walks you through a browser OAuth authorization and prints a
long-lived token valid for one year). Copy the token and paste it into the
Secrets page. Nothing rotates this token, so it does not go stale the way a
copied session credentials file does — a session file's refresh token is
invalidated as soon as the machine that exported it refreshes again.

#### Option B: Use the install script

If you prefer, run the install script instead. It uses your current kubectl
context — set `KUBECONFIG` explicitly so it targets the right cluster (easy
trap if you also run a local cluster like Rancher Desktop):

```bash
KUBECONFIG=~/.kube/pai-nuc.yaml bin/set-claude-token.sh kubectl agent-platform
```

On macOS, Claude Code stores credentials in the Keychain rather than a
file; export them first:

```bash
security find-generic-password -s "Claude Code-credentials" -w > /tmp/claude-creds.json
KUBECONFIG=~/.kube/pai-nuc.yaml CLAUDE_CREDENTIALS_FILE=/tmp/claude-creds.json \
  bin/set-claude-token.sh kubectl agent-platform
rm /tmp/claude-creds.json
```

This reads `~/.claude/.credentials.json` (or the path in `CLAUDE_CREDENTIALS_FILE`)
and creates the `claude-credentials` secret in the cluster.

After running, refresh the browser to clear the gate.

## Smoke Test

Once the secrets gate is satisfied:

1. In the UI, navigate to **Agents**
2. Click **hello-world** (the seed agent)
3. In the prompt field, enter: `Say OK`
4. Click **Run now**
5. Watch the live transcript as the agent executes and replies with exactly `OK`

This confirms the entire platform loop is working: UI → API → dispatcher → Kafka → agent task → result collection → live feed.

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

**Cause:** Dispatcher is not consuming tasks, or Kafka topics are not created.

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

   You should see `run.requests`, `run.events`, `run.transcript`, and `run.dlq`.

3. If topics are missing, the topics job may have failed. Re-run the Helm install to trigger a fresh job.

### Reset the admin password

**Symptom:** Admin password lost, or you want to change it. There is no
change-password UI yet (planned for milestone 02); the password exists only
as an argon2 hash in postgres.

**Fix:** Delete the admin principal, then reload the UI — the first-launch
setup page returns and lets you choose a new password:

```bash
kubectl -n agent-platform exec ap-postgresql-0 -- \
  env PGPASSWORD=$(kubectl -n agent-platform get secret ap-postgresql \
    -o jsonpath='{.data.postgres-password}' | base64 -d) \
  psql -U postgres -d agentplatform -c "DELETE FROM principals WHERE name='admin';"
```

Existing browser sessions are invalidated the next time they hit an
authenticated endpoint.
