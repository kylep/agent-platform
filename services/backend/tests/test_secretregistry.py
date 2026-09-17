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


# --- image-generation provider keys (docs/design/23) -------------------------

def test_image_provider_secrets_declared():
    # Three optional blocks the image_gen tool binds; a missing one degrades
    # to "provider not configured", so none may be `required`.
    reg = SecretRegistry(REPO_SECRETS)
    for name in ("openai-api-key", "gemini-api-key", "bfl-api-key"):
        info = reg.get(name)
        assert info is not None and info.error is None, name
        assert not info.spec.required
        assert info.spec.verifiable
        assert all(k.hint for k in info.spec.keys), f"{name}: every key needs a where-to-get-it hint"
    openai = reg.get("openai-api-key").spec
    assert [k.name for k in openai.keys] == ["OPENAI_API_KEY"]
    assert openai.verify.probe.url == "https://api.openai.com/v1/models"
    assert interpolate(openai.verify.probe.headers["Authorization"],
                       {"OPENAI_API_KEY": "sk-x"}) == "Bearer sk-x"
    gemini = reg.get("gemini-api-key").spec
    assert [k.name for k in gemini.keys] == ["GEMINI_API_KEY"]
    # Key goes in the x-goog-api-key header, never the URL: a key in a query
    # string lands in access logs, and newer-format keys reject `?key=`.
    assert gemini.verify.probe.url == "https://generativelanguage.googleapis.com/v1beta/models"
    assert "{" not in gemini.verify.probe.url
    assert interpolate(gemini.verify.probe.headers["x-goog-api-key"],
                       {"GEMINI_API_KEY": "AIza-x"}) == "AIza-x"
    bfl = reg.get("bfl-api-key")
    assert [k.name for k in bfl.spec.keys] == ["BFL_API_KEY"]
    assert bfl.spec.verify.script == "verify_bfl.py"
    assert (bfl.dir / "verify_bfl.py").is_file()


def _load_verify_bfl():
    import importlib.util
    path = REPO_SECRETS / "bfl-api-key" / "verify_bfl.py"
    spec = importlib.util.spec_from_file_location("verify_bfl", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_urlopen(status: int):
    import io
    import urllib.error

    class _Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def urlopen(req, timeout=None):
        urlopen.calls.append(req)
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(b"{}"))
        return _Resp(b"{}")
    urlopen.calls = []
    return urlopen


def test_verify_bfl_maps_statuses(monkeypatch, capsys):
    # BFL has no free "whoami": we poll a nonexistent task id, so a 404/422
    # (task unknown) and a 200 all prove the key authenticated; an auth
    # rejection fails, and anything else (rate limit, outage, WAF page) must
    # fail CLOSED rather than mark the key valid for the whole outage.
    mod = _load_verify_bfl()
    monkeypatch.setenv("BFL_API_KEY", "bfl-secret-value")
    for status, expect in ((401, 1), (403, 1), (404, 0), (422, 0), (200, 0),
                           (429, 1), (500, 1), (502, 1), (503, 1)):
        fake = _fake_urlopen(status)
        monkeypatch.setattr(mod.urllib.request, "urlopen", fake)
        rc = mod.main()
        out = capsys.readouterr().out
        assert rc == expect, (status, out)
        assert out.count("\n") == 1, "one detail line"
        if status not in (200, 404, 422):
            assert str(status) in out, "the detail names the code so the UI shows why"
        if status not in (200, 401, 403, 404, 422):
            assert "inconclusive" in out
        assert "bfl-secret-value" not in out
        req = fake.calls[0]
        assert req.full_url == ("https://api.bfl.ai/v1/get_result"
                                "?id=00000000-0000-0000-0000-000000000000")
        assert req.get_header("X-key") == "bfl-secret-value"


def test_verify_bfl_empty_key_and_unreachable(monkeypatch, capsys):
    import urllib.error
    mod = _load_verify_bfl()
    monkeypatch.setenv("BFL_API_KEY", "  ")
    assert mod.main() == 1 and "empty" in capsys.readouterr().out
    monkeypatch.setenv("BFL_API_KEY", "k")

    def down(req, timeout=None):
        raise urllib.error.URLError("dns")
    monkeypatch.setattr(mod.urllib.request, "urlopen", down)
    assert mod.main() == 1 and "unreachable" in capsys.readouterr().out
