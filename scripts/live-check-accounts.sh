#!/usr/bin/env bash
# Live check for design 40 (human accounts): registers a throwaway user,
# proves the fence and server-side logout against the running platform, then
# deletes the user. Prints one PASS/FAIL line per check and exits non-zero on
# any FAIL. Needs AGENT_PLATFORM_ADMIN (source exports.sh); AP_URL defaults
# to http://pai:8090.
set -uo pipefail
AP_URL="${AP_URL:-http://pai:8090}"
: "${AGENT_PLATFORM_ADMIN:?source exports.sh first}"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
admin="$tmp/admin.jar" user="$tmp/user.jar"
fails=0

check() {  # check <name> <expected-status> <actual-status>
  if [[ "$2" == "$3" ]]; then echo "PASS $1 ($3)"; else echo "FAIL $1 (want $2, got $3)"; fails=$((fails+1)); fi
}
status() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

check "anonymous POST /api/projects refused" 401 \
  "$(status -X POST -H 'content-type: application/json' -d '{"slug":"live-check","name":"x"}' "$AP_URL/api/projects")"
check "anonymous GET /api/help/topics refused" 401 "$(status "$AP_URL/api/help/topics")"

body=$(jq -n --arg p "$AGENT_PLATFORM_ADMIN" '{principal:"admin",password:$p}')
check "admin login" 200 "$(status -c "$admin" -H 'content-type: application/json' -d "$body" "$AP_URL/api/login")"
check "admin /api/me" 200 "$(status -b "$admin" "$AP_URL/api/me")"
check "admin still reaches /api/agents" 200 "$(status -b "$admin" "$AP_URL/api/agents")"
check "registration open" 200 \
  "$(status -b "$admin" -X PUT -H 'content-type: application/json' -d '{"open":true}' "$AP_URL/api/settings/registration")"

name="livecheck$RANDOM"
reg=$(jq -n --arg u "$name" '{username:$u,password:"x",confirm:"x"}')
code=$(status -c "$user" -H 'content-type: application/json' -d "$reg" "$AP_URL/api/register")
[[ "$code" == 200 || "$code" == 201 ]] && code=ok
check "register $name" ok "$code"
role=$(curl -s -b "$user" "$AP_URL/api/me" | jq -r .role)
check "new user has role user" user "$role"

for path in /api/agents /api/runs /api/whoami /api/help/topics /api/users /api/secrets; do
  check "user fenced from $path" 403 "$(status -b "$user" "$AP_URL$path")"
done
check "user setup-state shows no secrets" 0 "$(curl -s -b "$user" "$AP_URL/api/setup-state" | jq '.secrets | length')"

cp "$user" "$tmp/stolen.jar"
check "user logout" 200 "$(status -b "$user" -X POST "$AP_URL/api/logout")"
check "replayed cookie dead after logout" 401 "$(status -b "$tmp/stolen.jar" "$AP_URL/api/me")"

id=$(curl -s -b "$admin" "$AP_URL/api/users" | jq -r --arg u "$name" '.[] | select(.username==$u) | .id')
check "cleanup: delete $name" 200 "$(status -b "$admin" -X DELETE "$AP_URL/api/users/$id")"

echo "---"; echo "$fails failure(s)"; exit $(( fails > 0 ))
