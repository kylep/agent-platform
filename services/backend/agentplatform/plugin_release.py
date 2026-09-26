"""Verify the reviewed skills-only package before it enters the skill catalog."""
from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path

NAME = "agent-platform-coding"
# This digest is reviewed with the platform binary. File hashes inside the
# release manifest detect accidental drift; this pin also rejects a replaced
# manifest whose file hashes were recomputed to match tampered skills.
APPROVED_RELEASES = {
    "0.1.1": "3d2a0336ce7f970373c23c1728598ea4553027e9a852926aea5ceea1cb59f4c7",
}
# SHA-256 of the signed workflow artifact from run 36195106940. Admission
# reconstructs the same deterministic bundle from the runtime checkout, so
# a reviewed source manifest alone cannot stand in for the attested release.
ATTESTED_BUNDLES = {
    "0.1.1": "733fca6e6b80dc4d89cbd73f5f6d2d94c110336c24f0ddd5f87ee8e3527a37d0",
}
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


def release_bundle(root: Path) -> bytes:
    """Reproduce the CI tar/gzip subject, including file modes and ordering."""
    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w", format=tarfile.GNU_FORMAT) as archive:
        for path in (root, *sorted(root.rglob("*"))):
            info = archive.gettarinfo(str(path), arcname=str(path.relative_to(root.parent)))
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            # The synced Kubernetes volume sets the setgid bit on folders.
            # Git does not preserve that bit, and it is not release content.
            info.mode &= 0o777
            if path.is_file():
                with path.open("rb") as source:
                    archive.addfile(info, source)
            else:
                archive.addfile(info)
    # `gzip -n` is also used by the attestation workflow. Python's gzip
    # encoder produces different deflate bytes for the same tar stream.
    return subprocess.run(["gzip", "-n"], input=tar_bytes.getvalue(),
                          capture_output=True, check=True).stdout


def verify_release(root: Path) -> list[Path]:
    """Return verified skill dirs. Reject extras, symlinks, drift and authority."""
    release_path = root / "release.json"
    if not release_path.is_file() or release_path.is_symlink():
        raise ValueError("missing skills-only release manifest")
    release_bytes = release_path.read_bytes()
    release = json.loads(release_bytes)
    if (not isinstance(release, dict)
            or set(release) != {"name", "version", "files"}
            or release["name"] != NAME
            or not isinstance(release["version"], str)):
        raise ValueError("invalid plugin release identity")
    expected_digest = APPROVED_RELEASES.get(release["version"])
    if expected_digest is None or hashlib.sha256(release_bytes).hexdigest() != expected_digest:
        raise ValueError("plugin release is not approved by this platform build")
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
    if hashlib.sha256(release_bundle(root)).hexdigest() != ATTESTED_BUNDLES.get(
            release["version"]):
        raise ValueError("plugin bytes differ from the attested release bundle")
    return [root / path.rsplit("/", 1)[0] for path in sorted(expected)
            if SKILL_PATH.fullmatch(path)]
