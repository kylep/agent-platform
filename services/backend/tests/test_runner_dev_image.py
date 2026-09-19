"""services/runner/Dockerfile.dev — the Workbench image (docs/design/24).

CI never builds this image (several GB; see the `runner` job in ci.yaml), so
these pins are the only guard against it drifting from the versions the rest
of the repo runs on: the Playwright base must match `@playwright/test` in
services/web/package.json (the browsers the web tests expect), and the
claude-code tag must match the lean runner's so a dev run and an ordinary run
never disagree about the CLI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEV_DOCKERFILE = REPO_ROOT / "services" / "runner" / "Dockerfile.dev"
LEAN_DOCKERFILE = REPO_ROOT / "services" / "runner" / "Dockerfile"
WEB_PACKAGE_JSON = REPO_ROOT / "services" / "web" / "package.json"


def _dev() -> str:
    return DEV_DOCKERFILE.read_text()


def _claude_code_tag(dockerfile: str) -> str:
    m = re.search(r"@anthropic-ai/claude-code@(\S+)", dockerfile)
    assert m, "no @anthropic-ai/claude-code@<tag> pin"
    return m.group(1)


def test_base_tag_matches_web_playwright_version():
    version = json.loads(WEB_PACKAGE_JSON.read_text())["devDependencies"]["@playwright/test"]
    assert re.fullmatch(r"\^?\d+\.\d+\.\d+", version), version
    expected = f"mcr.microsoft.com/playwright:v{version.lstrip('^')}-noble"
    froms = re.findall(r"^FROM\s+(\S+)", _dev(), flags=re.MULTILINE)
    assert froms, "no FROM line"
    assert all(f == expected for f in froms), (froms, expected)


def test_claude_code_tag_matches_lean_image():
    assert _claude_code_tag(_dev()) == _claude_code_tag(LEAN_DOCKERFILE.read_text())


def test_playwright_mcp_is_pinned_exactly():
    """A bump is a deliberate edit of both this pin and the Dockerfile (the
    package bundles its own playwright-core; see the --executable-path note)."""
    m = re.search(r"@playwright/mcp@(\S+)", _dev())
    assert m, "no @playwright/mcp@<version> pin"
    assert m.group(1) == "0.0.82"


def test_no_secret_material():
    text = _dev()
    assert "ANTHROPIC_API_KEY" not in text
    assert "sk-ant-" not in text


def test_runtime_shape():
    """What workbench.py and the launcher rely on: the venv leads PATH, both
    runner modules ship, the .pth names the checked-out repo, the browser
    symlink exists for @playwright/mcp, and the image runs as `runner` — the
    base's uid-1001 user renamed, since that uid is every runner pod's."""
    text = _dev()
    assert re.search(r"^ENV PATH=/opt/venv/bin:\$PATH$", text, flags=re.MULTILINE)
    assert re.search(r"^COPY services/runner/runner\.py services/runner/workbench\.py /app/$",
                     text, flags=re.MULTILINE)
    assert "/workspace/repo/services/backend" in text and "/workspace/repo/sdk" in text
    assert "/opt/chromium/chrome" in text
    assert "usermod -l runner -d /home/runner -m pwuser" in text
    assert re.search(r"^USER runner$", text, flags=re.MULTILINE)
    assert 'ENTRYPOINT ["python3", "/app/runner.py"]' in text
