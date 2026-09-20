#!/usr/bin/env bash
# Install native Codex ChatGPT OAuth state as the platform's refreshable
# codex-credentials secret.
# Modes:  pre-boot   set-codex-auth.sh kubectl [namespace]
#         post-boot  AP_URL=http://pai:8090 AP_COOKIE_JAR=~/.ap-cookies set-codex-auth.sh api
set -euo pipefail
MODE="${1:-kubectl}"
NS="${2:-agent-platform}"
CREDS="${CODEX_AUTH_FILE:-$HOME/.codex/auth.json}"
[ -f "$CREDS" ] || { echo "No credentials at $CREDS (set CODEX_AUTH_FILE)"; exit 1; }
jq -e 'type == "object"' "$CREDS" >/dev/null || { echo "$CREDS is not a JSON object"; exit 1; }
case "$MODE" in
  kubectl)
    kubectl -n "$NS" create secret generic codex-credentials \
      --from-file=auth.json="$CREDS" \
      --dry-run=client -o yaml | kubectl apply -f -
    echo "Secret codex-credentials applied to namespace $NS" ;;
  api)
    : "${AP_URL:?set AP_URL, e.g. http://pai:8090}"
    curl -sf -b "${AP_COOKIE_JAR:-$HOME/.ap-cookies}" -X PUT \
      -H 'Content-Type: application/json' \
      --data "{\"data\":{\"auth.json\":$(jq -Rs . < "$CREDS")}}" \
      "$AP_URL/api/secrets/codex-credentials" >/dev/null
    echo "Secret set via API" ;;
  *) echo "usage: $0 kubectl [ns] | api"; exit 2 ;;
esac
