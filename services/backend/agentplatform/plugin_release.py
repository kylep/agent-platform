"""Verify the reviewed skills-only package before it enters the skill catalog."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

NAME = "agent-platform-coding"
MAX_FILE_BYTES = 64 * 1024
SKILL_PATH = re.compile(r"skills/[a-z][a-z0-9-]{0,63}/SKILL\.md$")
MANIFEST_PATHS = {
    "plugin.json", ".codex-plugin/plugin.json", ".claude-plugin/plugin.json",
}
MANIFEST_FIELDS = {
    "plugin.json": {"name", "version", "description", "skills"},
    ".claude-plugin/plugin.json": {"name", "version", "description", "author"},
    ".codex-plugin/plugin.json": {
        "name", "version", "description", "author", "skills", "interface",
    },
}
INTERFACE_FIELDS = {
    "displayName", "shortDescription", "longDescription", "developerName",
    "category", "capabilities", "defaultPrompt",
}


def verify_release(root: Path) -> list[Path]:
    """Return verified skill dirs. Reject extras, symlinks, drift and authority."""
    release_path = root / "release.json"
    if not release_path.is_file() or release_path.is_symlink():
        raise ValueError("missing skills-only release manifest")
    release = json.loads(release_path.read_text())
    if set(release) != {"name", "version", "files"} or release["name"] != NAME:
        raise ValueError("invalid plugin release identity")
    files = release["files"]
    if not isinstance(files, dict) or not files:
        raise ValueError("empty plugin release")
    expected = set(files)
    if not MANIFEST_PATHS.issubset(expected) or not all(
            path in MANIFEST_PATHS or SKILL_PATH.fullmatch(path) for path in expected):
        raise ValueError("plugin release contains a forbidden path")
    actual = set()
    for entry in root.rglob("*"):
        if entry.is_symlink():
            raise ValueError("plugin release contains a symlink")
        if entry.is_file():
            actual.add(entry.relative_to(root).as_posix())
    if actual != expected | {"release.json"}:
        raise ValueError("plugin release file set differs from review")
    for path, digest in files.items():
        data = (root / path).read_bytes()
        if len(data) > MAX_FILE_BYTES or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"plugin release checksum differs: {path}")
    for path in MANIFEST_PATHS:
        manifest = json.loads((root / path).read_text())
        if manifest.get("name") != NAME or manifest.get("version") != release["version"]:
            raise ValueError(f"plugin manifest identity differs: {path}")
        if set(manifest) - MANIFEST_FIELDS[path]:
            raise ValueError(f"plugin manifest grants authority: {path}")
        if path != ".claude-plugin/plugin.json" and manifest.get("skills") != "./skills/":
            raise ValueError(f"plugin skill path differs: {path}")
        if path == ".codex-plugin/plugin.json":
            interface = manifest.get("interface", {})
            if not isinstance(interface, dict) or set(interface) - INTERFACE_FIELDS \
                    or interface.get("capabilities") != []:
                raise ValueError("Codex plugin interface is not skills-only")
    return [root / path.rsplit("/", 1)[0] for path in sorted(expected)
            if SKILL_PATH.fullmatch(path)]
