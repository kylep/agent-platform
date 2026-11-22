import base64
import gzip
import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from agentplatform import backup_export
from agentplatform.backup_export import inspect_archive, write_archive


async def test_connection_is_admin_only_and_never_returns_key(token_client, admin_client, secret_store):
    assert (await token_client.get("/api/backups/connection")).status_code == 401
    before = (await admin_client.get("/api/backups/connection")).json()
    assert before["ready"] is False
    payload = {
        "bucket": "kp-pai-memory-backups", "prefix": "agent-platform",
        "age_recipient": "age1" + "a" * 58,
        "service_account_json": json.dumps({
            "type": "service_account", "client_email": "backup@example.iam.gserviceaccount.com",
            "private_key": "test-only-private-key"}),
    }
    assert (await admin_client.put("/api/backups/connection", json=payload)).status_code == 200
    saved = (await admin_client.get("/api/backups/connection")).json()
    assert saved["ready"] and saved["credential_set"]
    assert "private_key" not in json.dumps(saved)
    assert (await secret_store.get("backup-gcs"))["service_account_json"] == payload["service_account_json"]
    assert (await admin_client.put("/api/backups/connection", json={
        **payload, "bucket": "Bad Bucket", "service_account_json": None,
    })).status_code == 422


async def test_backup_routes_reject_unconfigured_run(admin_client):
    assert (await admin_client.get("/api/backups")).json() == []
    response = await admin_client.post("/api/backups/run")
    assert response.status_code == 422


@pytest.mark.skipif(not shutil.which("age") or not shutil.which("age-keygen"),
                    reason="age CLI unavailable")
def test_archive_round_trip_and_tamper_detection(tmp_path: Path):
    identity = tmp_path / "identity.txt"
    subprocess.run(["age-keygen", "-o", str(identity)], check=True, capture_output=True)
    recipient = subprocess.run(["age-keygen", "-y", str(identity)], check=True,
                               capture_output=True, text=True).stdout.strip()
    dump = tmp_path / "database.sql.gz"
    dump.write_bytes(gzip.compress(b"CREATE TABLE sample (id int);\n"))
    target = tmp_path / "ap-recovery-20260930T120000Z.tar.age"
    write_archive(dump, target, recipient, "agent-platform", [{
        "name": "discord-kai-bot", "type": "Opaque",
        "data": {"token": base64.b64encode(b"test-token").decode()},
    }], created_at="2026-09-30T12:00:00+00:00")
    result = inspect_archive(target, identity.read_text())
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert result["secret_names"] == ["discord-kai-bot"]
    assert result["database_bytes"] == dump.stat().st_size
    assert b"test-token" not in target.read_bytes()
    broken = tmp_path / "broken.age"
    broken.write_bytes(target.read_bytes()[:-100])
    with pytest.raises(ValueError):
        inspect_archive(broken, identity.read_text())


@pytest.mark.skipif(not shutil.which("age") or not shutil.which("age-keygen"),
                    reason="age CLI unavailable")
def test_extract_pauses_restore_of_an_older_archive(tmp_path: Path, monkeypatch):
    identity = tmp_path / "identity.txt"
    subprocess.run(["age-keygen", "-o", str(identity)], check=True, capture_output=True)
    recipient = subprocess.run(["age-keygen", "-y", str(identity)], check=True,
                               capture_output=True, text=True).stdout.strip()
    dump = tmp_path / "database.sql.gz"
    dump.write_bytes(gzip.compress(b"CREATE TABLE sample (id int);\n"))
    target = tmp_path / "old.tar.age"
    # An older exporter encrypted the dump without a restore marker.
    monkeypatch.setattr(backup_export, "append_restore_marker", lambda _path: None)
    write_archive(dump, target, recipient, "agent-platform", [],
                  created_at="2026-09-30T12:00:00+00:00")
    output = tmp_path / "extracted"
    subprocess.run([
        sys.executable, str(Path(__file__).resolve().parents[3] / "scripts" / "backup_restore.py"),
        "extract", str(target), "--identity", str(identity), "--output", str(output),
    ], check=True, capture_output=True)
    restored_sql = gzip.decompress((output / "database.sql.gz").read_bytes())
    assert b"CREATE TABLE sample" in restored_sql
    assert b"CREATE TABLE IF NOT EXISTS public.platform_maintenance" in restored_sql
    assert b"VALUES (1, 'restore'" in restored_sql


def test_export_refuses_a_dump_over_the_restore_limit(tmp_path: Path, monkeypatch):
    from agentplatform import backup_export
    dump = tmp_path / "database.sql.gz"
    dump.write_bytes(b"x" * 10)
    backup_export.check_dump_size(dump)
    monkeypatch.setattr(backup_export, "MAX_DATABASE_BYTES", 5)
    with pytest.raises(RuntimeError, match="restore limit"):
        backup_export.check_dump_size(dump)


def _helm_secret(name: str, version: int, status: str, values: dict) -> dict:
    release = {"name": name, "version": version, "info": {"status": status},
               "chart": {"metadata": {"version": "0.1.0"}}, "config": values}
    helm = base64.b64encode(gzip.compress(json.dumps(release).encode()))
    return {"name": f"sh.helm.release.v1.{name}.v{version}", "type": "helm.sh/release.v1",
            "data": {"release": base64.b64encode(helm).decode()}}


def test_deployment_record_keeps_the_deployed_values_and_images():
    record = backup_export.deployment_record(
        [_helm_secret("ap", 145, "superseded", {"old": True}),
         _helm_secret("ap", 146, "deployed", {"images": {"backend": {"tag": "ud-x"}}}),
         {"name": "plain", "type": "Opaque", "data": {}}],
        [{"name": "api", "images": ["agent-platform-backend:ud-x", "ghostunnel:1"]},
         {"name": "web", "images": ["agent-platform-web:ud-x"]}])
    assert record["releases"] == [{"release": "ap", "revision": 146, "chart": "0.1.0",
                                   "values": {"images": {"backend": {"tag": "ud-x"}}}}]
    assert record["images"] == ["agent-platform-backend:ud-x", "agent-platform-web:ud-x",
                                "ghostunnel:1"]


@pytest.mark.skipif(not shutil.which("age") or not shutil.which("age-keygen"),
                    reason="age CLI unavailable")
def test_archive_carries_the_deployment_record_and_extract_writes_it(tmp_path: Path):
    identity = tmp_path / "identity.txt"
    subprocess.run(["age-keygen", "-o", str(identity)], check=True, capture_output=True)
    recipient = subprocess.run(["age-keygen", "-y", str(identity)], check=True,
                               capture_output=True, text=True).stdout.strip()
    dump = tmp_path / "database.sql.gz"
    dump.write_bytes(gzip.compress(b"CREATE TABLE sample (id int);\n"))
    target = tmp_path / "ap-recovery-20261116T120000Z.tar.age"
    deployment = {"releases": [{"release": "ap", "revision": 7, "chart": "0.1.0",
                                "values": {"env": {"AP_SESSION_SECRET": "s3cret-value"}}}],
                  "images": ["agent-platform-backend:ud-x"]}
    write_archive(dump, target, recipient, "agent-platform", [],
                  created_at="2026-11-16T12:00:00+00:00", deployment=deployment)
    assert b"s3cret-value" not in target.read_bytes()
    result = inspect_archive(target, identity.read_text())
    assert result["deployment"] == {"releases": ["ap"], "images": 1}
    output = tmp_path / "extracted"
    subprocess.run([
        sys.executable, str(Path(__file__).resolve().parents[3] / "scripts" / "backup_restore.py"),
        "extract", str(target), "--identity", str(identity), "--output", str(output),
    ], check=True, capture_output=True)
    assert json.loads((output / "deployment.json").read_text()) == deployment
    assert stat.S_IMODE((output / "deployment.json").stat().st_mode) == 0o600


def test_backup_status_flags_stale_shrunk_and_leftovers(tmp_path: Path):
    from agentplatform.api.backups import backup_status
    import os
    assert backup_status(tmp_path / "missing", 0) is None
    now = 1_800_000_000.0
    for name, size, age_h in (("ap-recovery-20261115T083000Z.tar.age", 1000, 50),
                              ("ap-recovery-20261116T083000Z.tar.age", 800, 30)):
        path = tmp_path / name
        path.write_bytes(b"x" * size)
        os.utime(path, (now - age_h * 3600, now - age_h * 3600))
        (tmp_path / (name + ".json")).write_text(json.dumps({"name": name}))
    (tmp_path / "ap-20261116T080000Z.sql.gz.tmp").write_bytes(b"")
    status = backup_status(tmp_path, now)
    assert status["newest"] == "ap-recovery-20261116T083000Z.tar.age"
    assert status["newest_age_hours"] == 30.0 and status["stale"] and status["shrunk"]
    assert status["failed_leftovers"] == ["ap-20261116T080000Z.sql.gz.tmp"]
    fresh = tmp_path / "ap-recovery-20261117T083000Z.tar.age"
    fresh.write_bytes(b"x" * 900)
    os.utime(fresh, (now - 3600, now - 3600))
    (tmp_path / (fresh.name + ".json")).write_text(json.dumps({"name": fresh.name}))
    status = backup_status(tmp_path, now)
    assert not status["stale"] and not status["shrunk"]
