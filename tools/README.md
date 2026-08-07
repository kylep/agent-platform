# tools/ — custom platform tools

The executable building block of the agent-platform
(`docs/design/12-executable-capabilities.md`; the reader-facing version is
`docs/building-blocks/tools.md`). Each directory here is one tool that the
**mcp-broker** offers to agents over MCP and the **tool-executor** runs in a
locked-down subprocess — two platform services, both defined in
`docs/building-blocks/glossary.md`. Agents get a menu of named actions instead
of a shell.

    tools/<name>/
      tool.yaml          # manifest: description, JSON-schema params, infra
      run.py             # entrypoint: JSON args on stdin, output on stdout
      requirements.txt   # optional pip deps (baked into the executor image by CI)
      test_run.py        # optional unit test (CI tools job)

Four worked examples ship in this directory: `stocks` (third-party HTTP API),
`discord_chat` (a declared secret), `linear` (multi-action dispatch on one
tool), and `memory` (a tool that owns a Postgres schema). Copy the closest one.

Rules of the game:

- The model controls **arguments only** — `run.py` is trusted code that
  arrived through pull-request review, which is exactly why agents may trigger
  it without a shell.
- `run.py` gets a minimal env: the declared secrets' keys, `TOOL_DB_URL` when
  `infra.database: true`, and `TOOL_CALLER_AGENT` / `TOOL_RUN_ID` (the caller
  identity the broker verified, not anything the model supplied). Nothing
  else — never assume `os.environ` carries more.
- Exit non-zero with a message on stderr to signal an error to the model.
- Output is capped at 256 KiB; keep results tight and structured (JSON).
- A new pip dependency means the executor image must be rebuilt; `tool.yaml`
  and `run.py` edits go live on the next sync of the platform's git checkout,
  with no deploy.

Authoring happens in the UI (Skills & Tools → New tool) or by hand; either way
changes land as a pull request, like every other configuration change
(`docs/building-blocks/changes.md`).
