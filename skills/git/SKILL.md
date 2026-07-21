---
name: git
description: Clone, branch, commit, and push to a Git remote over HTTPS using a token supplied via GIT_ASKPASS (never in a URL or argv). Use when an agent needs to make changes in a repo and open them as a branch/PR.
secrets:
  - github-token
---
# git

Make changes in a Git repository safely and land them on a branch. The push
credential is a token bound to this skill (`github-token`); it is fed to git via
`GIT_ASKPASS` so it never appears in a URL, argv, or process listing.

## Setup

Write an askpass helper and point git at it:

```bash
ASKPASS=$(mktemp)
printf '#!/bin/sh\nprintf "%%s" "$GIT_TOKEN"\n' > "$ASKPASS"
chmod +x "$ASKPASS"
export GIT_ASKPASS="$ASKPASS" GIT_TERMINAL_PROMPT=0
export GIT_TOKEN="$GITHUB_TOKEN"   # bound from the github-token secret
```

Use an https remote with an `x-access-token` username (no secret in the URL):

```bash
git clone --depth 1 "https://x-access-token@github.com/OWNER/REPO.git" repo
cd repo
```

## Make a change on a branch

```bash
git checkout -b my-branch
# ...edit files...
git add -A
git -c user.name="agent" -c user.email="agent@agent-platform.local" \
    commit -m "describe the change"
git push origin +HEAD:my-branch          # force-update the branch
```

## Notes

- Always work on a branch and open a pull request for review — never push to the
  default branch directly.
- Pin host keys (`StrictHostKeyChecking=yes` with a known_hosts file) if you use
  SSH instead of the HTTPS+token path above.
- Keep commit messages short and specific; they are read in review.
