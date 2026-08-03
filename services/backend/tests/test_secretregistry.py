"""The secrets/ registry, verification, and heartbeat (docs/design/10)."""
import agentplatform.secretverify as sv
from agentplatform.secretregistry import SecretRegistry
from agentplatform.secretverify import interpolate, verify_secret
from agentplatform.secrets import InMemorySecretStore
from agentplatform.verifierloop import SecretVerifier
from tests.conftest import REPO_SECRETS


# --- the shipped registry ----------------------------------------------------

def test_repo_registry_loads_all_platform_secrets():
    reg = SecretRegistry(REPO_SECRETS)
    names = {i.name for i in reg.list()}
    # Core platform secrets must exist; wizard-declared ones (linear-api-key…)
    # may accumulate — declaring a new secret must NOT break this test.
    assert names >= {"claude-credentials", "github-app", "github-token",
                     "discord-bot", "discord-webhook"}
    assert all(i.spec is not None and i.error is None for i in reg.list())
    assert "claude-credentials" in reg.required()
    # claude is run-verified (no runnable check); github-app verifies by script
    assert not reg.get("claude-credentials").spec.verifiable
    assert reg.get("github-app").spec.verify.script == "verify_github_app.py"
    assert (reg.get("github-app").dir / "verify_github_app.py").is_file()
    # probe secrets declare a single key = the env var skills read
    assert reg.get("github-token").spec.keys[0].name == "GITHUB_TOKEN"
    assert reg.get("discord-bot").spec.keys[0].name == "token"


def test_registry_surfaces_broken_yaml(tmp_path):
    d = tmp_path / "busted"; d.mkdir()
    (d / "secret.yaml").write_text("verify:\n  probe: {url: x}\n  script: y\n")
    info = SecretRegistry(tmp_path).get("busted")
    assert info.spec is None and "exactly one" in info.error


# --- probe interpolation -----------------------------------------------------

def test_interpolate_substitutes_and_dedupes_scheme_prefix():
    assert interpolate("Bearer {t}", {"t": "abc"}) == "Bearer abc"
    # pasted "Bot " prefix into a "Bot {token}" template isn't doubled
    assert interpolate("Bot {t}", {"t": "Bot abc"}) == "Bot abc"
    assert interpolate("Bot {t}", {"t": "bot abc"}) == "Bot abc"
    assert interpolate("{url}", {"url": " https://x "}) == "https://x"


def test_probe_missing_key_and_bad_url_are_invalid():
    reg = SecretRegistry(REPO_SECRETS)
    r = sv._run_probe(reg.get("discord-webhook").spec.verify.probe, {})
    assert r.status == "invalid" and "DISCORD_WEBHOOK_URL" in r.detail
    r = sv._run_probe(reg.get("discord-webhook").spec.verify.probe,
                      {"DISCORD_WEBHOOK_URL": "not-a-url"})
    assert r.status == "invalid" and "not http" in r.detail


# --- sandboxed verify scripts ------------------------------------------------

def _script_secret(tmp_path, script_body: str):
    d = tmp_path / "scripted"; d.mkdir()
    (d / "secret.yaml").write_text("verify:\n  script: verify_check.py\n")
    (d / "verify_check.py").write_text(script_body)
    return SecretRegistry(tmp_path).get("scripted")


async def test_verify_script_pass_fail_and_env_isolation(tmp_path, monkeypatch):
    # The subprocess env must hold ONLY this secret's data (+PATH): the script
    # fails if it can see the parent's env, passes when it sees its own key.
    monkeypatch.setenv("AP_DB_URL", "postgres://leak")
    info = _script_secret(tmp_path, (
        "import os, sys\n"
        "assert 'AP_DB_URL' not in os.environ, 'leaked parent env'\n"
        "ok = os.environ.get('token') == 'good'\n"
        "print('checked' if ok else 'bad token')\n"
        "sys.exit(0 if ok else 1)\n"))
    r = await verify_secret(info, {"token": "good"})
    assert r.status == "valid" and r.detail == "checked"
    r = await verify_secret(info, {"token": "wrong"})
    assert r.status == "invalid" and r.detail == "bad token"


async def test_verify_script_missing_file(tmp_path):
    d = tmp_path / "ghost"; d.mkdir()
    (d / "secret.yaml").write_text("verify:\n  script: verify_nope.py\n")
    r = await verify_secret(SecretRegistry(tmp_path).get("ghost"), {"k": "v"})
    assert r.status == "invalid" and "not found" in r.detail


# --- the heartbeat -----------------------------------------------------------

async def test_verifier_heartbeat_writes_statuses(sf, monkeypatch):
    from sqlalchemy import select
    from agentplatform.db import SecretMeta
    store = InMemorySecretStore()
    await store.set("discord-bot", {"token": "abc"})
    monkeypatch.setattr(sv, "http_probe",
                        lambda url, headers: (200, "ok") if "discord.com" in url else (401, "no"))
    v = SecretVerifier(SecretRegistry(REPO_SECRETS), store, sf)
    results = await v.verify_all()
    # set + probe ok → valid; unset probeables → missing; claude untouched (run-verified)
    assert results["discord-bot"] == "valid"
    assert results["github-token"] == "missing"
    assert results["discord-webhook"] == "missing"
    assert results["github-app"] == "missing"
    assert "claude-credentials" not in results
    async with sf() as s:
        rows = {m.name: m.status for m in (await s.execute(select(SecretMeta))).scalars()}
    assert rows["discord-bot"] == "valid" and rows["github-token"] == "missing"
