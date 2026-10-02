"""Create a portable, age-encrypted Agent Platform recovery archive.

The scheduled Job receives only an age *recipient* and an upload-only GCS
credential. Its Kubernetes identity can read namespace Secrets for the brief
duration of the backup. No plaintext archive or decryption key is written to
the backup volume or sent to Cloud Storage.
"""
from __future__ import annotations

import base64
import fcntl
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ARCHIVE_VERSION = 1
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,221}[a-z0-9]$")
PREFIX_RE = re.compile(r"^[a-zA-Z0-9/_-]{0,180}$")
RECIPIENT_RE = re.compile(r"^age1[023456789acdefghjklmnpqrstuvwxyz]{58}$")


def validate_destination(bucket: str, prefix: str, recipient: str) -> None:
    if not BUCKET_RE.fullmatch(bucket):
        raise ValueError("invalid Cloud Storage bucket name")
    if not PREFIX_RE.fullmatch(prefix) or "//" in prefix or ".." in prefix:
        raise ValueError("invalid object prefix")
    if not RECIPIENT_RE.fullmatch(recipient):
        raise ValueError("invalid age recipient")


# Appended to the pg_dump so every restore lands in maintenance mode. pg_dump
# sets search_path to empty, so the table must be qualified with public.
RESTORE_MARKER_SQL = (
    "\n-- agent-platform: a restore starts in maintenance mode\n"
    "CREATE TABLE IF NOT EXISTS public.platform_maintenance ("
    "id integer PRIMARY KEY, mode varchar(16) NOT NULL DEFAULT 'running', "
    "reason text NOT NULL DEFAULT '', entered_at timestamptz, "
    "resumed_by varchar(128));\n"
    "INSERT INTO public.platform_maintenance (id, mode, reason, entered_at, resumed_by) "
    "VALUES (1, 'restore', 'restored from a backup', CURRENT_TIMESTAMP, NULL) "
    "ON CONFLICT (id) DO UPDATE SET mode = excluded.mode, reason = excluded.reason, "
    "entered_at = excluded.entered_at, resumed_by = NULL;\n")


def append_restore_marker(dump: Path) -> None:
    """Append the marker as a second gzip member (concatenated members are one
    valid gzip stream). Only the dump's copy changes; the live database never does."""
    with dump.open("ab") as out:
        out.write(gzip.compress(RESTORE_MARKER_SQL.encode()))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _namespace_secrets(namespace: str) -> list[dict]:
    from kubernetes import client, config

    config.load_incluster_config()
    rows = client.CoreV1Api().list_namespaced_secret(namespace).items
    return [
        {"name": row.metadata.name, "type": row.type, "data": row.data or {}}
        for row in rows if row.type == "Opaque"
    ]


def _add_bytes(archive: tarfile.TarFile, name: str, content: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = 0o600
    archive.addfile(info, io.BytesIO(content))


def write_archive(dump: Path, target: Path, recipient: str,
                  namespace: str, secrets: list[dict], *, created_at: str) -> dict:
    """Stream the dump and Secret values through tar into age; persist ciphertext only."""
    if not dump.is_file() or dump.stat().st_size == 0:
        raise ValueError("PostgreSQL dump is missing or empty")
    append_restore_marker(dump)     # before the checksum below covers it
    # Kubernetes returns Secret data as base64 strings. Validate before an
    # apparently successful archive can preserve malformed secret values.
    for item in secrets:
        for value in item["data"].values():
            base64.b64decode(value, validate=True)
    secret_bytes = json.dumps(secrets, sort_keys=True, separators=(",", ":")).encode()
    manifest = {
        "format": "agent-platform-recovery", "version": ARCHIVE_VERSION,
        "created_at": created_at, "namespace": namespace,
        "database": {"path": "database.sql.gz", "sha256": _sha256(dump),
                     "bytes": dump.stat().st_size},
        "secrets": {"path": "secrets.json", "sha256": hashlib.sha256(secret_bytes).hexdigest(),
                    "count": len(secrets)},
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    if target.exists() or temp.exists():
        raise FileExistsError(target)
    process = subprocess.Popen(["age", "-r", recipient, "-o", str(temp)],
                               stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.stdin is not None
        with tarfile.open(fileobj=process.stdin, mode="w|") as archive:
            _add_bytes(archive, "manifest.json", json.dumps(manifest, sort_keys=True).encode())
            archive.add(dump, arcname="database.sql.gz", recursive=False)
            _add_bytes(archive, "secrets.json", secret_bytes)
        _, error = process.communicate()
        if process.returncode:
            raise RuntimeError(f"age encryption failed: {error.decode(errors='replace')[:300]}")
        # The API mounts this PVC read-only as supplementary group 1001 so an
        # administrator can download the ciphertext. Keep it inaccessible to
        # other UIDs while allowing that group to read it.
        os.chmod(temp, 0o640)
        temp.replace(target)
    except BaseException:
        process.kill() if process.poll() is None else None
        process.wait()
        temp.unlink(missing_ok=True)
        raise
    return manifest


def inspect_archive(encrypted: Path, identity: str) -> dict:
    """Authenticate/decrypt a recovery file and return a redacted manifest.

    The private identity is a temporary local file because age CLI requires an
    identity path. It is never put in the archive, database, Secret store or
    job environment; the API removes it before returning.
    """
    if (len(identity) > 4096 or not any(line.startswith("AGE-SECRET-KEY-1")
                                        for line in identity.splitlines())):
        raise ValueError("select an age recovery identity file")
    with tempfile.TemporaryDirectory(prefix="ap-backup-inspect-") as work:
        key = Path(work) / "identity"
        key.write_text(identity)
        key.chmod(0o600)
        proc = subprocess.Popen(["age", "-d", "-i", str(key), str(encrypted)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        manifest: dict | None = None
        secret_names: list[str] = []
        observed: dict[str, str] = {}
        try:
            assert proc.stdout is not None
            with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
                for member in archive:
                    if member.name not in {"manifest.json", "database.sql.gz", "secrets.json"}:
                        raise ValueError("archive contains an unexpected file")
                    if member.name in observed or not member.isfile():
                        raise ValueError("archive has duplicate or non-file entries")
                    if member.size > (512 * 1024 * 1024 if member.name == "database.sql.gz"
                                      else 16 * 1024 * 1024):
                        raise ValueError("archive member exceeds its size limit")
                    source = archive.extractfile(member)
                    assert source is not None
                    digest = hashlib.sha256()
                    small = bytearray()
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                        if member.name != "database.sql.gz":
                            small.extend(chunk)
                    observed[member.name] = digest.hexdigest()
                    if member.name == "manifest.json":
                        manifest = json.loads(small)
                    elif member.name == "secrets.json":
                        records = json.loads(small)
                        if (not isinstance(records, list) or any(
                            not isinstance(record, dict) or not isinstance(record.get("name"), str)
                            or not isinstance(record.get("data"), dict)
                            for record in records
                        )):
                            raise ValueError("invalid Secret list")
                        secret_names = [record["name"] for record in records]
            proc.stdout.close()
            _, error = proc.communicate()
            if proc.returncode:
                raise ValueError(f"decryption failed: {error.decode(errors='replace')[:180]}")
        except BaseException as error:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            if isinstance(error, tarfile.TarError):
                raise ValueError("decryption failed or archive is invalid") from error  # noqa: TRY004
            raise
    if (not isinstance(manifest, dict)
            or manifest.get("format") != "agent-platform-recovery"
            or manifest.get("version") != ARCHIVE_VERSION
            or set(observed) != {"manifest.json", "database.sql.gz", "secrets.json"}):
        raise ValueError("not a complete Agent Platform recovery archive")
    if (not isinstance(manifest.get("database"), dict)
            or not isinstance(manifest.get("secrets"), dict)
            or not isinstance(manifest.get("created_at"), str)
            or not isinstance(manifest.get("namespace"), str)
            or not isinstance(manifest["database"].get("sha256"), str)
            or not isinstance(manifest["database"].get("bytes"), int)
            or not isinstance(manifest["secrets"].get("sha256"), str)
            or not isinstance(manifest["secrets"].get("count"), int)):
        raise ValueError("archive manifest is invalid")  # noqa: TRY004
    if (observed["database.sql.gz"] != manifest["database"]["sha256"]
            or observed["secrets.json"] != manifest["secrets"]["sha256"]
            or len(secret_names) != manifest["secrets"]["count"]):
        raise ValueError("archive checksum or Secret count mismatch")
    return {"format": manifest["format"], "version": manifest["version"],
            "created_at": manifest["created_at"], "namespace": manifest["namespace"],
            "database_bytes": manifest["database"]["bytes"],
            "secret_count": len(secret_names), "secret_names": sorted(secret_names)}


def export() -> None:
    output = Path(os.environ.get("AP_BACKUP_OUTPUT_DIR", "/backups"))
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".cloud-backup.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another cloud backup is already running") from error
        _export_locked(output)


def _export_locked(output: Path) -> None:
    from google.cloud import storage

    config_dir = Path(os.environ.get("AP_BACKUP_CONNECTION_DIR", "/backup-connection"))
    required = ("bucket", "prefix", "age_recipient", "service_account_json")
    if not all((config_dir / name).is_file() for name in required):
        raise RuntimeError("Backup connection is incomplete; set it in Settings → Connections")
    bucket, prefix, recipient = ((config_dir / name).read_text().strip()
                                 for name in required[:3])
    validate_destination(bucket, prefix, recipient)
    credential = json.loads((config_dir / "service_account_json").read_text())
    if credential.get("type") != "service_account" or not credential.get("client_email"):
        raise ValueError("backup credential must be a Google service-account JSON key")
    namespace = os.environ.get("AP_K8S_NAMESPACE", "agent-platform")
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    name = f"ap-recovery-{stamp}.tar.age"
    target = output / name
    dump = Path(os.environ.get("AP_BACKUP_DUMP_PATH", "/work/database.sql.gz"))
    secrets = _namespace_secrets(namespace)
    manifest = write_archive(dump, target, recipient, namespace, secrets,
                             created_at=now.isoformat())
    object_name = "/".join(part for part in (prefix.strip("/"), name) if part)
    client = storage.Client.from_service_account_info(credential)
    client.bucket(bucket).blob(object_name).upload_from_filename(
        str(target), content_type="application/octet-stream",
        if_generation_match=0, timeout=600)
    receipt = {"name": name, "created_at": manifest["created_at"],
               "bytes": target.stat().st_size, "sha256": _sha256(target),
               "gcs_uri": f"gs://{bucket}/{object_name}",
               "secret_count": manifest["secrets"]["count"]}
    receipt_path = target.with_name(name + ".json")
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n")
    os.chmod(receipt_path, 0o640)
    print(json.dumps({"event": "backup_uploaded", **receipt}), flush=True)
    keep = max(1, int(os.environ.get("AP_BACKUP_LOCAL_KEEP", "14")))
    for old in sorted(output.glob("ap-recovery-*.tar.age"), reverse=True)[keep:]:
        old.unlink(missing_ok=True)
        old.with_name(old.name + ".json").unlink(missing_ok=True)


if __name__ == "__main__":
    export()
