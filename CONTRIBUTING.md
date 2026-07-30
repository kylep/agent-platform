# Contributing

## One-time setup: secret-leak prevention

This repo is public, so a committed secret is exposed the instant it's pushed —
prevention has to happen **before** the push, on your machine. Install the
pre-commit hooks once per clone:

```sh
pip install pre-commit
pre-commit install --hook-type pre-commit --hook-type pre-push
```

That wires three checks that run on every commit and push (see
`.pre-commit-config.yaml`):

- **forbid-secret-files** — blocks never-commit files by name (editor swap files,
  `*.pem`/keys, `.env`, `exports.sh`). Catches what content scanners can't: a
  base64-encoded key or a vim `.exports.sh.swp` has no secret *marker* to match,
  but the file itself is the leak.
- **detect-private-key** — raw PEM private keys.
- **gitleaks** — general secret/token/high-entropy content scanning.

CI runs the *same* hooks (`secret-scan` job) as a gating backstop, so a missing
local install still can't merge a secret — but by then a push to `main` has
already exposed it, which is why the local hooks are the real control.

Secrets live only in your local `exports.sh` (gitignored). Never `git add -A` in
this repo; stage explicit paths.
