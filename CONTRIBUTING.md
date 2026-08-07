# Contributing

The agent-platform is a personal project with a single maintainer; there is no
review rota or CLA. Almost everything worth knowing about *how* to change it
lives elsewhere: `README.md` for what the platform is, `docs/setup.md` for
getting an install running, `docs/deployment.md` for how a change reaches the
cluster, and `docs/building-blocks/` for the concepts (start with the
Glossary). Configuration changes — agents, skills, tools, secret shapes — are
made through the UI or by editing git, and always land as pull requests.

The one thing this file exists to enforce is below, and it applies to every
clone.

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
