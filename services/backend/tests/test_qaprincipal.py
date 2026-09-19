"""The `qa` principal (docs/design/25): a reader row whose password the API
mints once at boot and stores in the `qa-web-login` secret, never shows, and
never logs. Rotation is "delete the secret's value, clear the mark"."""
import logging

import pytest
from sqlalchemy import select

from agentplatform.api.auth import ph
from agentplatform.db import Principal, SchemaMark
from agentplatform.qaprincipal import (QA_MARK, QA_PASSWORD_KEY, QA_PRINCIPAL,
                                       QA_SECRET, QA_USER_KEY,
                                       ensure_qa_principal)
from agentplatform.secretregistry import SecretRegistry
from agentplatform.secrets import InMemorySecretStore
from tests.conftest import REPO_SECRETS


class RecordingStore(InMemorySecretStore):
    def __init__(self):
        super().__init__()
        self.writes: list[tuple[str, dict]] = []

    async def set(self, name, data):
        self.writes.append((name, dict(data)))
        await super().set(name, data)


async def _qa_row(sf) -> Principal | None:
    async with sf() as s:
        return (await s.execute(select(Principal).where(
            Principal.name == QA_PRINCIPAL))).scalar_one_or_none()


async def _marked(sf) -> bool:
    async with sf() as s:
        return await s.get(SchemaMark, QA_MARK) is not None


async def test_first_boot_mints_the_row_and_the_secret_once(sf, caplog):
    store = RecordingStore()
    with caplog.at_level(logging.INFO):
        out = await ensure_qa_principal(sf, store)
    row = await _qa_row(sf)
    assert row is not None and row.role == "reader"
    assert len(store.writes) == 1 and store.writes[0][0] == QA_SECRET
    data = await store.get(QA_SECRET)
    assert set(data) == {QA_USER_KEY, QA_PASSWORD_KEY}
    assert data[QA_USER_KEY] == QA_PRINCIPAL
    pw = data[QA_PASSWORD_KEY]
    assert len(pw) >= 24
    # The hash on the row opens with the password in the secret — and only the
    # secret ever carries it: not the return value, not a log line.
    assert ph.verify(row.password_hash, pw)
    assert pw not in out
    assert pw not in caplog.text
    assert await _marked(sf)
    # Idempotent: a second boot writes nothing.
    await ensure_qa_principal(sf, store)
    assert len(store.writes) == 1
    assert (await _qa_row(sf)).password_hash == row.password_hash


async def test_existing_row_is_adopted_and_the_secret_left_alone(sf):
    async with sf() as s:
        s.add(Principal(name=QA_PRINCIPAL, role="reader", password_hash=ph.hash("theirs")))
        await s.commit()
    store = RecordingStore()
    await store.set(QA_SECRET, {QA_USER_KEY: "qa", QA_PASSWORD_KEY: "theirs"})
    store.writes.clear()
    out = await ensure_qa_principal(sf, store)
    assert store.writes == []
    assert ph.verify((await _qa_row(sf)).password_hash, "theirs")
    assert "theirs" not in out
    assert await _marked(sf)


async def test_cleared_mark_and_empty_secret_rotates(sf, caplog):
    # The documented rotation: delete the secret's value and clear the mark.
    # The STORED SECRET is the source of truth — a row whose password is not
    # in it is a row nobody can log in as. The row stays (its name is what
    # the pod's env and the login form use), its hash is replaced, the secret
    # filled again, and the boot says so at WARNING without the password.
    async with sf() as s:
        s.add(Principal(name=QA_PRINCIPAL, role="reader", password_hash=ph.hash("old")))
        await s.commit()
    store = RecordingStore()
    with caplog.at_level(logging.WARNING, logger="qaprincipal"):
        await ensure_qa_principal(sf, store)
    row = await _qa_row(sf)
    new = (await store.get(QA_SECRET))[QA_PASSWORD_KEY]
    assert new != "old" and ph.verify(row.password_hash, new)
    assert len(store.writes) == 1
    warned = [r for r in caplog.records if r.levelno == logging.WARNING and "rotated" in r.getMessage()]
    assert len(warned) == 1
    assert new not in caplog.text and "old" not in warned[0].getMessage()
    # The other half-state — mark cleared, row deleted, secret still full —
    # mints a fresh row AND rewrites the secret: the stored password matches
    # no hash, and a hash nobody can match is not a login.
    async with sf() as s:
        await s.delete(await s.get(Principal, row.id))
        await s.delete(await s.get(SchemaMark, QA_MARK))
        await s.commit()
    await ensure_qa_principal(sf, store)
    fresh = await _qa_row(sf)
    assert fresh is not None and len(store.writes) == 2
    assert ph.verify(fresh.password_hash, (await store.get(QA_SECRET))[QA_PASSWORD_KEY])


async def test_mark_short_circuits_even_without_a_row(sf):
    # The mark IS the off-switch (like every other seed): an admin who
    # deleted the row keeps it deleted until they clear the mark.
    async with sf() as s:
        s.add(SchemaMark(name=QA_MARK))
        await s.commit()
    store = RecordingStore()
    await ensure_qa_principal(sf, store)
    assert (await _qa_row(sf)) is None and store.writes == []


async def test_secret_write_failure_leaves_no_row_and_no_mark(sf):
    class Broken(InMemorySecretStore):
        async def set(self, name, data):
            raise RuntimeError("apiserver down")
    with pytest.raises(RuntimeError):
        await ensure_qa_principal(sf, Broken())
    assert (await _qa_row(sf)) is None and not await _marked(sf)


# --- the declared block ------------------------------------------------------

def test_qa_web_login_block_is_declared():
    reg = SecretRegistry(REPO_SECRETS)
    info = reg.get(QA_SECRET)
    assert info is not None and info.error is None
    spec = info.spec
    assert not spec.required
    assert [k.name for k in spec.keys] == [QA_USER_KEY, QA_PASSWORD_KEY]
    assert "qa-principal-v1" in spec.hint
    assert spec.verify.script == "verify_qa_login.py"
    assert (info.dir / "verify_qa_login.py").is_file()


def _load_verify():
    import importlib.util
    path = REPO_SECRETS / QA_SECRET / "verify_qa_login.py"
    spec = importlib.util.spec_from_file_location("verify_qa_login", path)
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
        return _Resp(b'{"ok": true}')
    urlopen.calls = []
    return urlopen


def test_verify_qa_login_maps_statuses(monkeypatch, capsys):
    import json
    mod = _load_verify()
    monkeypatch.setenv(QA_USER_KEY, "qa")
    monkeypatch.setenv(QA_PASSWORD_KEY, "minted-secret-value")
    monkeypatch.setenv("AP_API_URL", "http://api.test:8000/")
    for status, expect in ((200, 0), (401, 1), (403, 1), (500, 1), (502, 1)):
        fake = _fake_urlopen(status)
        monkeypatch.setattr(mod.urllib.request, "urlopen", fake)
        rc = mod.main()
        out = capsys.readouterr().out
        assert rc == expect, (status, out)
        assert out.count("\n") == 1, "one detail line"
        assert "minted-secret-value" not in out
        req = fake.calls[0]
        assert req.full_url == "http://api.test:8000/api/login"
        assert json.loads(req.data) == {"principal": "qa", "password": "minted-secret-value"}


def test_verify_qa_login_defaults_to_the_local_api_and_reports_missing(monkeypatch, capsys):
    import urllib.error
    mod = _load_verify()
    monkeypatch.delenv("AP_API_URL", raising=False)
    monkeypatch.setenv(QA_USER_KEY, "qa")
    monkeypatch.setenv(QA_PASSWORD_KEY, " ")
    assert mod.main() == 1 and "missing" in capsys.readouterr().out
    monkeypatch.setenv(QA_PASSWORD_KEY, "pw")
    fake = _fake_urlopen(200)
    monkeypatch.setattr(mod.urllib.request, "urlopen", fake)
    assert mod.main() == 0
    capsys.readouterr()
    assert fake.calls[0].full_url == "http://127.0.0.1:8000/api/login"

    def down(req, timeout=None):
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(mod.urllib.request, "urlopen", down)
    assert mod.main() == 1 and "unreachable" in capsys.readouterr().out


async def test_rotated_row_logs_in_with_the_new_secret_value(client, sf, secret_store):
    # End to end through the route: a stale row with an empty secret is
    # rotated at boot and the value the pod would read opens the console.
    async with sf() as s:
        s.add(Principal(name=QA_PRINCIPAL, role="reader", password_hash=ph.hash("old")))
        await s.commit()
    await ensure_qa_principal(sf, secret_store)
    pw = (await secret_store.get(QA_SECRET))[QA_PASSWORD_KEY]
    assert (await client.post("/api/login", json={"principal": "qa", "password": "old"})).status_code == 401
    assert (await client.post("/api/login", json={"principal": "qa", "password": pw})).status_code == 200
    who = (await client.get("/api/whoami")).json()
    assert (who["principal"], who["role"]) == ("qa", "reader")
