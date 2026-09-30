import base64
import gzip
import json
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
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
