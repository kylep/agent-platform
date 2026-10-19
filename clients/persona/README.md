# ap-persona

Install the reviewed local package with `pipx install ./clients/persona`.
Usage, enrollment, privacy, compatibility and rollout are documented in
[Local persona sessions](../../docs/building-blocks/local-persona-sessions.md).

```sh
ap-persona enroll --persona pai --origin https://ap.example
ap-persona launch --persona pai --harness claude --cwd /path/to/work
ap-persona resume --persona pai --harness claude <session-id>
ap-persona sync --recover <session-id>
```

Tests use synthetic records and a disposable PostgreSQL instance:

```sh
services/backend/.venv/bin/python -m pytest clients/persona/tests/test_persona.py
bin/ap-persona-postgres-test
```

There is no global hook installation and no permanent companion process.

Before adding a Codex version to the allowlist, run the explicit macOS native
probe (requires that exact CLI on PATH):

```sh
services/backend/.venv/bin/python clients/persona/tests/probe_codex.py --expected-version 0.163.0
```

The probe runs real Codex and helper hooks against local AP/MCP/model fixtures.
It checks launch and resume context injection, transcript capture, shell
credential/environment/network fences, and rejected-bootstrap gating. It uses
no real enrollment or model calls and makes no global configuration edits.
Use `--cwd /path/to/work` to also check a particular project's configuration.
After pulling reviewed support, refresh the installed helper with
`pipx install --force ./clients/persona`.
