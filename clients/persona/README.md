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
