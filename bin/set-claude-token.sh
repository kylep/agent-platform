#!/usr/bin/env bash
# bin/set-claude-token.sh — install Claude subscription credentials as the
# platform's claude-credentials secret.
# Modes:  pre-boot   set-claude-token.sh kubectl [namespace]
#         post-boot  AP_URL=http://pai:8090 AP_COOKIE_JAR=~/.ap-cookies set-claude-token.sh api
set -euo pipefail
MODE="${1:-kubectl}"
NS="${2:-agent-platform}"
CREDS="${CLAUDE_CREDENTIALS_FILE:-$HOME/.claude/.credentials.json}"
[ -f "$CREDS" ] || { echo "No credentials at $CREDS (set CLAUDE_CREDENTIALS_FILE)"; exit 1; }
case "$MODE" in
  kubectl)
    kubectl -n "$NS" create secret generic claude-credentials \
      --from-file=credentials.json="$CREDS" \
      --dry-run=client -o yaml | kubectl apply -f -
    echo "Secret claude-credentials applied to namespace $NS" ;;
  api)
    : "${AP_URL:?set AP_URL, e.g. http://pai:8090}"
    curl -sf -b "${AP_COOKIE_JAR:-$HOME/.ap-cookies}" -X PUT \
      -H 'Content-Type: application/json' \
      --data "{\"data\":{\"credentials.json\":$(jq -Rs . < "$CREDS")}}" \
      "$AP_URL/api/secrets/claude-credentials" >/dev/null
    echo "Secret set via API" ;;
  *) echo "usage: $0 kubectl [ns] | api"; exit 2 ;;
esac
