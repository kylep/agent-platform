#!/bin/bash
# Reference deploy script (last run 2026-09-18 for QA-16, helm rev 59). Copy into the session scratchpad, edit the image list, run from Terminal.app or directly if ssh pai works.
# Run from a Terminal.app window (LAN access). Idempotent; safe to rerun.
set -u
export DOCKER_HOST="unix://$HOME/.rd/docker.sock"
export KUBECONFIG="$HOME/.kube/pai-nuc.yaml"
S=${SCRATCH:?set SCRATCH to this session's scratchpad dir}
cd /Users/kp/gh/agent-platform || exit 1
step() { echo; echo "### $(date +%H:%M:%S) $*"; }

step "web build (prebuilt into the image)"
(cd services/web && npm run -s build) || { echo "WEB BUILD FAILED"; exit 1; }

build() { # name context [dockerfile]
  local name=$1 ctx=$2 df=${3:-}
  step "build $name"
  if [ -n "$df" ]; then
    docker buildx build --platform linux/amd64 --provenance=false --load -t "agent-platform-$name:dev" -f "$df" "$ctx" 2>&1 | tail -3
  else
    docker buildx build --platform linux/amd64 --provenance=false --load -t "agent-platform-$name:dev" "$ctx" 2>&1 | tail -3
  fi
  [ "${PIPESTATUS[0]}" = 0 ] || { echo "BUILD FAILED: $name"; exit 1; }
  docker save -o "$S/img-$name.tar" "agent-platform-$name:dev" || exit 1
  ls -la "$S/img-$name.tar" | awk '{print $5, $9}'
}
build backend services/backend
build web services/web services/web/Dockerfile.prebuilt
build mcp-broker services/mcp-broker



step "ship + import into k3s containerd (k8s.io namespace)"
for name in backend web mcp-broker; do
  scp -o ConnectTimeout=8 "$S/img-$name.tar" "pai:/tmp/img-$name.tar" || exit 1
  ssh -o ConnectTimeout=8 pai "sudo k3s ctr -n k8s.io images import /tmp/img-$name.tar >/dev/null && rm -f /tmp/img-$name.tar && echo imported $name" || exit 1
done

step "stored values (fresh) + helm upgrade (never --reuse-values)"
helm get values ap -n agent-platform > "$S/ap-stored-values.yaml" || exit 1
helm upgrade ap charts/agent-platform -n agent-platform \
  -f "$S/ap-stored-values.yaml" -f charts/agent-platform/values-pai-nuc.yaml \
  --timeout 10m 2>&1 | tail -5
helm history ap -n agent-platform | tail -2

step "rollout restart (same tag, new image bytes)"
kubectl -n agent-platform rollout restart deploy/ap-api deploy/ap-dispatcher deploy/ap-recorder deploy/ap-web deploy/ap-mcp-broker
for d in ap-api ap-dispatcher ap-recorder ap-web ap-mcp-broker; do
  kubectl -n agent-platform rollout status "deploy/$d" --timeout=300s || echo "ROLLOUT FAILED: $d"
done
step "facade last (it reads the API's OpenAPI at boot)"
kubectl -n agent-platform rollout restart deploy/ap-mcp-facade
kubectl -n agent-platform rollout status deploy/ap-mcp-facade --timeout=180s || echo "ROLLOUT FAILED: ap-mcp-facade"

step "post-deploy state"
kubectl -n agent-platform get pods | grep -v -E "Running|Completed" | grep -v "^run-" || echo "all platform pods Running/Completed"
kubectl -n agent-platform get pod -l app.kubernetes.io/component=api -o jsonpath='{.items[*].spec.containers[*].name}'; echo
kubectl -n agent-platform exec ap-kafka-controller-0 -- /opt/bitnami/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list 2>/dev/null | grep -E "artifacts|wiki" | tr '\n' ' '; echo
kubectl -n agent-platform logs deploy/ap-api --since=3m 2>&1 | grep -i -E "artifact|artist|art-channel|error|traceback" | tail -8 | cut -c1-200
kubectl -n agent-platform logs deploy/ap-dispatcher --since=3m 2>&1 | grep -i -E "artifact|artist|grant|error|traceback" | tail -8 | cut -c1-200
echo "### DONE-DEPLOY"
