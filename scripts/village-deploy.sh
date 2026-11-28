#!/bin/sh
# Village deploy (GEN-11): build backend + web under one unique tag, import into
# pai's k3s, retag runner-dev, helm upgrade with the live values plus explicit
# tags, village.enabled and global concurrency. See docs/deployment.md.
#
#   KUBECONFIG_PAI=/path/to/pai.kubeconfig TAG=kp-$(git rev-parse --short HEAD) \
#     PREV_RUNNER_DEV=kp-<previous tag> sh scripts/village-deploy.sh
#
# PREV_RUNNER_DEV is a runner-dev tag already imported on pai (from
# `helm get values ap`); runner-dev is never rebuilt here.
set -eu
cd "$(dirname "$0")/.."
TAG=${TAG:?set TAG}
K=${KUBECONFIG_PAI:?set KUBECONFIG_PAI}
PREV=${PREV_RUNNER_DEV:?set PREV_RUNNER_DEV}
LOG=${TMPDIR:-/tmp}/village-deploy-$TAG
mkdir -p "$LOG"
docker buildx build --platform linux/amd64 --provenance=false --load -t agent-platform-backend:$TAG services/backend > "$LOG/backend.log" 2>&1 || { echo BUILD_FAILED backend; tail -20 "$LOG/backend.log"; exit 1; }
(npm run build -w web && npm run build:storybook -w web) > "$LOG/web-npm.log" 2>&1 || { echo BUILD_FAILED web-npm; tail -20 "$LOG/web-npm.log"; exit 1; }
docker buildx build --platform linux/amd64 --provenance=false --load -t agent-platform-web:$TAG -f services/web/Dockerfile.prebuilt services/web > "$LOG/web.log" 2>&1 || { echo BUILD_FAILED web; tail -20 "$LOG/web.log"; exit 1; }
for img in agent-platform-backend agent-platform-web; do
  docker save $img:$TAG | ssh pai 'sudo k3s ctr -n k8s.io images import -' > /dev/null; echo "IMPORTED $img:$TAG"
done
ssh pai "sudo k3s ctr -n k8s.io images tag --force docker.io/library/agent-platform-runner-dev:$PREV docker.io/library/agent-platform-runner-dev:$TAG >/dev/null" && echo "TAGGED runner-dev:$TAG"
V=$(mktemp)
trap 'rm -f "$V"' EXIT
helm --kubeconfig "$K" -n agent-platform get values ap | tail -n +2 > "$V"
helm --kubeconfig "$K" upgrade ap charts/agent-platform -n agent-platform -f "$V" \
  --set images.backend.tag=$TAG --set images.web.tag=$TAG --set images.runnerDev.tag=$TAG \
  --set village.enabled=true --set env.AP_GLOBAL_CONCURRENCY=8 2>&1 | grep -E 'REVISION|STATUS|Error' || true
for d in ap-api ap-dispatcher ap-recorder ap-web ap-village; do
  kubectl --kubeconfig "$K" -n agent-platform rollout status deploy/$d --timeout=300s 2>&1 | tail -1
done
kubectl --kubeconfig "$K" -n agent-platform rollout restart deploy/ap-mcp-facade >/dev/null
kubectl --kubeconfig "$K" -n agent-platform rollout status deploy/ap-mcp-facade --timeout=180s | tail -1
kubectl --kubeconfig "$K" get deploy -n agent-platform -o custom-columns=N:.metadata.name,I:.spec.template.spec.containers[0].image | grep -E 'api|dispatcher|web|village'
kubectl --kubeconfig "$K" -n agent-platform logs deploy/ap-village --tail=5 2>&1 | tail -5
echo DEPLOY_DONE
