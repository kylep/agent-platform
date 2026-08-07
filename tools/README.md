# tools/ — custom platform tools (docs/design/12)

The executable building block. Each directory is one MCP tool the broker
serves to agents and the tool-executor runs:

    tools/<name>/
      tool.yaml          # manifest: description, JSON-schema params, infra
      run.py             # entrypoint: JSON args on stdin, output on stdout
      requirements.txt   # optional pip deps (baked into the executor image by CI)
      test_run.py        # optional unit test (CI tools job)

Rules of the game:

- The model controls **arguments only** — run.py is trusted code that arrived
  through PR review, which is exactly why agents may trigger it without a shell.
- `run.py` gets a minimal env: the declared secrets' keys, TOOL_DB_URL when
  `infra.database: true`, and TOOL_CALLER_AGENT / TOOL_RUN_ID (broker-verified).
  Nothing else — never assume os.environ carries anything.
- Exit non-zero with a message on stderr to signal an error to the model.
- Output is capped at 256 KiB; keep results tight and structured (JSON).
- New pip dependency ⇒ executor image rebuild; tool.yaml/run.py edits are live
  on the next checkout sync.

Authoring happens in the UI (Skills & Tools → New tool) or by hand; either
way changes land through the standard PR change loop.
