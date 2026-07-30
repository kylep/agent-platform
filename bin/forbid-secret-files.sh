#!/usr/bin/env sh
# Block files that must never be committed, by NAME — the last line of defense
# behind .gitignore. Content scanners (gitleaks) miss these: an editor swap file
# or a base64-encoded private key has no "BEGIN PRIVATE KEY" marker to match, yet
# the file itself is the leak. This is exactly how `.exports.sh.swp` (holding the
# admin password + a base64 GitHub App key) reached the public repo once.
#
# Used as a pre-commit/pre-push hook (pre-commit passes staged paths as args);
# with no args it scans everything currently tracked. POSIX sh + `grep -E` so it
# runs the same on macOS (BSD) and Linux CI.
set -eu

# Extended-regex (ERE) over the full path. Add patterns here, not exceptions.
FORBIDDEN='(^|/)\.?[^/]*\.sw[a-p]$|(^|/)exports\.sh$|(^|/)\.exports\.sh|\.pem$|\.p12$|\.pfx$|(^|/)id_(rsa|ed25519|ecdsa)$|(^|/)\.env$|(^|/)\.env\.|credentials\.json$|(^|/)\.netrc$'
# Legitimately-tracked exceptions (templates/samples).
ALLOW='exports\.sh\.sample$|\.env\.sample$|\.env\.example$'

if [ "$#" -gt 0 ]; then
  list() { for f in "$@"; do printf '%s\n' "$f"; done; }
  files=$(list "$@")
else
  files=$(git ls-files)
fi

bad=$(printf '%s\n' "$files" \
  | grep -E "$FORBIDDEN" 2>/dev/null \
  | grep -Ev "$ALLOW" 2>/dev/null || true)

if [ -n "$bad" ]; then
  echo "BLOCKED: these files must never be committed (secrets / editor swap / keys):" >&2
  printf '%s\n' "$bad" | sed 's/^/  /' >&2
  echo "If one is a false positive, adjust bin/forbid-secret-files.sh; do not force it in." >&2
  exit 1
fi
