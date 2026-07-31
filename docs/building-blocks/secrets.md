# Secrets

**What:** credentials the platform and its skills use. Split deliberately in
two:

- **Shape** — everything *about* a secret — lives in **git**:
  `secrets/<name>/secret.yaml` declares its keys (which become env vars when a
  skill binds it), set-time hints, whether the platform requires it, and how
  to verify it. This is the single source of truth the Secrets UI, the API,
  the readiness gate, and the verifier heartbeat all read.
- **Values** live only in **k8s Secrets**, set through the API/UI. They never
  enter git. On a fresh cluster, git tells you exactly which secrets to set
  and how to check them; the values are the only thing you re-enter.

**secret.yaml shape:**

```yaml
name: github-token
description: GitHub token/PAT the git skill uses to clone and push.
required: false               # platform can't operate without it?
hint: "…"                     # set-time hint when keys can't express it
keys:
  - name: GITHUB_TOKEN        # the k8s data key = the env var skills read
    hint: "GitHub token/PAT with repo scope…"
verify:                       # exactly one of:
  probe: {url: "…", headers: {…}}   # declarative GET, 2xx = valid (default choice)
  script: verify_something.py       # sandboxed escape hatch (see below)
  run: true                         # verified by run outcomes (claude-credentials)
```

**Verification** is deterministic code, never an LLM:

- **Declarative probe** — `{key}` placeholders interpolate the secret's data
  into the URL/headers; a pasted auth-scheme prefix (`Bot …`) is deduped.
- **verify_*.py script** — for what a probe can't express (e.g. github-app
  signs a JWT). Runs in a sandboxed subprocess with ONLY that secret's data in
  its env: no other secrets, no DB, no platform config.
- A **heartbeat** in the dispatcher re-runs every verifiable secret on a
  cadence (`AP_SECRET_VERIFY_INTERVAL_SECONDS`, default 600) and records the
  status — `valid` can't go stale-green. The readiness gate also re-verifies a
  failing secret once at the moment of use (try-before-block), so transient
  failures and just-fixed secrets recover on their own.

**How to add one:** add a folder + PR (the New-Skill wizard scaffolds one when
a new skill needs a credential), then set its value on the Secrets page.

## exports.sh is not part of the platform

`exports.sh` (gitignored; see `exports.sh.sample`) is a **development
convenience**: it's how the operator hands secret values to Claude so Claude
can set them via the API while building. The running platform never reads it.
