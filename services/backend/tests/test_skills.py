import hashlib
import json
from pathlib import Path

from agentplatform.skills import SkillStore, parse_frontmatter

REVIEWED_SKILLS = 4


def _admit_pending_attestation(monkeypatch, package: Path) -> None:
    """A version's bundle digest is pinned only after the provenance workflow
    attests it on main (docs/agent-platform-coding-plugin.md). Until then,
    admit this checkout's own reproduction of it so the catalog mechanics stay
    tested; test_plugin_catalog_requires_the_attested_bundle holds the pin."""
    from agentplatform import plugin_release

    version = json.loads((package / "release.json").read_text())["version"]
    if version not in plugin_release.ATTESTED_BUNDLES:
        monkeypatch.setitem(plugin_release.ATTESTED_BUNDLES, version, hashlib.sha256(
            plugin_release.release_bundle(package)).hexdigest())


def _mk_skill(root: Path, name: str, body: str):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(body)


def test_parse_frontmatter():
    fm, body = parse_frontmatter("---\nname: git\nsecrets: [a, b]\n---\nUse git.\n")
    assert fm["name"] == "git" and fm["secrets"] == ["a", "b"]
    assert body.strip() == "Use git."
    # No frontmatter → empty dict, body unchanged.
    fm2, body2 = parse_frontmatter("just text")
    assert fm2 == {} and body2 == "just text"


def test_store_loads_skills_but_rejects_secret_authority(tmp_path):
    _mk_skill(tmp_path, "git", "---\nname: git\ndescription: Git ops\n---\nbody")
    _mk_skill(tmp_path, "discord", "---\nname: discord\nsecrets: [discord-webhook]\n---\nbody")
    _mk_skill(tmp_path, "no-frontmatter", "just a body, no yaml")
    store = SkillStore(tmp_path)
    names = {s.name for s in store.list()}
    assert {"git", "discord", "no-frontmatter"} <= names
    assert store.get("git").skill is not None
    assert store.get("discord").skill is None
    assert "secrets" in store.get("discord").error


def test_bad_frontmatter_quarantines(tmp_path):
    _mk_skill(tmp_path, "broken", "---\nsecrets: [unclosed\n---\nbody")
    store = SkillStore(tmp_path)
    info = store.get("broken")
    assert info is not None and info.error is not None and info.skill is None


def test_reviewed_plugin_skills_enter_catalog_only_when_release_matches(monkeypatch,
                                                                        tmp_path):
    import shutil

    from agentplatform.plugin_release import verify_release

    source = Path(__file__).resolve().parents[3] / "plugins" / "agent-platform-coding"
    root = tmp_path / "checkout"
    (root / "skills").mkdir(parents=True)
    package = root / "plugins" / "agent-platform-coding"
    shutil.copytree(source, package)
    _admit_pending_attestation(monkeypatch, package)
    assert len(verify_release(package)) == REVIEWED_SKILLS
    store = SkillStore(root / "skills")
    assert store.get("app-building").origin == "plugin"
    assert store.get("platform-change").origin == "plugin"
    assert store.get("platform-regression").skill is not None
    legacy = root / "skills" / "platform-change"
    legacy.mkdir()
    (legacy / "SKILL.md").write_text("---\nname: platform-change\n---\ncollision")
    store.reload()
    assert store.get("platform-change").skill is None
    legacy.joinpath("SKILL.md").unlink()
    legacy.rmdir()
    (package / "hooks.json").write_text("{}")
    store.reload()
    assert store.get("platform-change") is None
    assert "file set" in store.get("agent-platform-coding").error


def test_recomputed_plugin_manifest_does_not_approve_tampered_skill(tmp_path):
    import hashlib
    import json
    import shutil

    from agentplatform.plugin_release import verify_release

    source = Path(__file__).resolve().parents[3] / "plugins" / "agent-platform-coding"
    package = tmp_path / "plugins" / "agent-platform-coding"
    shutil.copytree(source, package)
    skill_path = "skills/platform-change/SKILL.md"
    skill = package / skill_path
    skill.write_text(skill.read_text() + "\nUnreviewed instruction.\n")
    manifest_path = package / "release.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][skill_path] = hashlib.sha256(skill.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    try:
        verify_release(package)
    except ValueError as exc:
        assert "not approved" in str(exc)
    else:
        raise AssertionError("a recomputed release manifest bypassed approval")


def test_plugin_catalog_requires_the_attested_bundle(monkeypatch, tmp_path):
    import hashlib
    import shutil

    from agentplatform import plugin_release

    source = Path(__file__).resolve().parents[3] / "plugins" / "agent-platform-coding"
    package = tmp_path / "plugins" / "agent-platform-coding"
    shutil.copytree(source, package)
    version = json.loads((package / "release.json").read_text())["version"]
    digest = hashlib.sha256(plugin_release.release_bundle(package)).hexdigest()
    pinned = plugin_release.ATTESTED_BUNDLES.get(version)
    if pinned is not None:
        assert digest == pinned
    else:
        # Approved for review but not yet attested on main: the catalog
        # refuses it until the attested digest is pinned.
        try:
            plugin_release.verify_release(package)
        except ValueError as exc:
            assert "attested release bundle" in str(exc)
        else:
            raise AssertionError("catalog admitted a release with no attestation pin")
        monkeypatch.setitem(plugin_release.ATTESTED_BUNDLES, version, digest)
    # Kubernetes' synced volume adds setgid to directories. That mount mode
    # must not change the identity of the reviewed Git release.
    for directory in (package, *(p for p in package.rglob("*") if p.is_dir())):
        directory.chmod(directory.stat().st_mode | 0o2000)
    assert len(plugin_release.verify_release(package)) == REVIEWED_SKILLS
    monkeypatch.setitem(plugin_release.ATTESTED_BUNDLES, version, "0" * 64)
    try:
        plugin_release.verify_release(package)
    except ValueError as exc:
        assert "attested release bundle" in str(exc)
    else:
        raise AssertionError("catalog admitted a package with no matching attestation")


def test_provenance_workflow_checks_everything_but_the_pending_pin(tmp_path):
    """The workflow builds the bundle it attests, so it verifies with
    `attested=False`: the approved manifest, file set and hashes still hold."""
    import shutil

    from agentplatform import plugin_release

    source = Path(__file__).resolve().parents[3] / "plugins" / "agent-platform-coding"
    package = tmp_path / "plugins" / "agent-platform-coding"
    shutil.copytree(source, package)
    assert len(plugin_release.verify_release(package, attested=False)) == REVIEWED_SKILLS
    skill = package / "skills" / "app-building" / "SKILL.md"
    skill.write_text(skill.read_text() + "\nUnreviewed instruction.\n")
    try:
        plugin_release.verify_release(package, attested=False)
    except ValueError as exc:
        assert "checksum differs" in str(exc)
    else:
        raise AssertionError("the workflow path admitted a drifted skill")


async def test_skills_api_lists_with_used_by(admin_client, sf):
    # The default test agent store has no skills wired, so used_by is empty but
    # the endpoint must still return 200 with the shape.
    r = await admin_client.get("/api/skills")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
