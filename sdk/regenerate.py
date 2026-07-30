#!/usr/bin/env python3
"""Regenerate the SDK from the app's OpenAPI spec.

The client under `agent_platform_sdk/` is GENERATED, never hand-edited: it is
derived from `create_app(...).openapi()` with openapi-python-client. Run this
after any API change. CI runs it and fails if the committed output differs
(`git diff --exit-code sdk/`), so the SDK can never drift from the API — the
spec is the single source of truth.

Requires the backend package importable and `openapi-python-client` installed
(both are in the backend `[dev]` extra). Usage:  python sdk/regenerate.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT.parent / "services" / "backend"
sys.path.insert(0, str(BACKEND))

from agentplatform.api.app import create_app  # noqa: E402
from agentplatform.config import Settings  # noqa: E402
from agentplatform.events import FakeProducer  # noqa: E402

PACKAGE = "agent_platform_sdk"


def main() -> None:
    spec = create_app(Settings(), None, FakeProducer()).openapi()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "openapi.json").write_text(json.dumps(spec, indent=2, sort_keys=True))
        # The console script lives next to the interpreter that has the package
        # installed; PATH may not include the venv bin under `python sdk/...`.
        exe = Path(sys.executable).parent / "openapi-python-client"
        gen = str(exe) if exe.exists() else "openapi-python-client"
        subprocess.run(
            [gen, "generate",
             "--path", str(tmp / "openapi.json"),
             "--meta", "none",
             "--config", str(ROOT / "openapi-config.yaml"),
             "--output-path", str(tmp / PACKAGE)],
            check=True, cwd=tmp)
        dst = ROOT / PACKAGE
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(tmp / PACKAGE, dst,
                        ignore=shutil.ignore_patterns("__pycache__", ".ruff_cache"))
    print(f"regenerated {dst.relative_to(ROOT.parent)}")


if __name__ == "__main__":
    main()
