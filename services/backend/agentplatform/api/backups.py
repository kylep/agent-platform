"""Admin backup connection and encrypted recovery exports.

One API serves Settings and the external MCP facade. The storage credential is
write-only in responses. Backup bytes are ciphertext and are served as a file,
never embedded in a JSON/MCP Tool result.
"""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agentplatform.api.auth import require_admin
from agentplatform.backup_export import inspect_archive, validate_destination
from agentplatform.db import SecretMeta

router = APIRouter(prefix="/api/backups", dependencies=[Depends(require_admin)])
CONNECTION_SECRET = "backup-gcs"
BACKUP_NAME = re.compile(r"^ap-recovery-\d{8}T\d{6}Z\.tar\.age$")
MAX_IMPORT_BYTES = 512 * 1024 * 1024


class BackupConnectionIn(BaseModel):
    bucket: str
    prefix: str = "agent-platform"
    age_recipient: str
    service_account_json: str | None = None


async def _connection(request: Request) -> dict[str, str]:
    return await request.app.state.secret_store.get(CONNECTION_SECRET) or {}


@router.get("/connection")
async def get_backup_connection(request: Request):
    data = await _connection(request)
    return {"bucket": data.get("bucket", ""), "prefix": data.get("prefix", "agent-platform"),
            "age_recipient": data.get("age_recipient", ""),
            "credential_set": bool(data.get("service_account_json")),
            "ready": all(data.get(key) for key in
                         ("bucket", "prefix", "age_recipient", "service_account_json"))}


@router.put("/connection")
async def put_backup_connection(request: Request, body: BackupConnectionIn):
    bucket, prefix, recipient = (body.bucket.strip(), body.prefix.strip("/ "),
                                 body.age_recipient.strip())
    try:
        validate_destination(bucket, prefix, recipient)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    current = await _connection(request)
    credential = (body.service_account_json or "").strip() or current.get("service_account_json", "")
    if credential:
        try:
            parsed = json.loads(credential)
            if (not isinstance(parsed, dict) or parsed.get("type") != "service_account"
                    or not parsed.get("client_email") or not parsed.get("private_key")):
                raise ValueError
        except (ValueError, TypeError) as error:
            raise HTTPException(422, "paste a Google service-account JSON key") from error
    await request.app.state.secret_store.set(CONNECTION_SECRET, {
        "bucket": bucket, "prefix": prefix, "age_recipient": recipient,
        "service_account_json": credential,
    })
    async with request.app.state.session_factory() as session:
        meta = await session.get(SecretMeta, CONNECTION_SECRET) or SecretMeta(name=CONNECTION_SECRET)
        meta.status = "unprobed"
        session.add(meta)
        await session.commit()
    return {"ok": True, "ready": bool(credential)}


@router.post("/connection/verify")
async def verify_backup_connection(request: Request):
    """Prove this exact credential can create an object; it need not read it."""
    from google.cloud import storage

    data = await _connection(request)
    if not all(data.get(key) for key in
               ("bucket", "prefix", "age_recipient", "service_account_json")):
        raise HTTPException(422, "complete the backup connection first")
    try:
        validate_destination(data["bucket"], data["prefix"], data["age_recipient"])
        credential = json.loads(data["service_account_json"])
        name = "/".join(part for part in (data["prefix"].strip("/"),
                                          "_verify", uuid.uuid4().hex + ".txt") if part)

        def write_marker():
            client = storage.Client.from_service_account_info(credential)
            client.bucket(data["bucket"]).blob(name).upload_from_string(
                "Agent Platform backup connection verified.\n",
                content_type="text/plain", if_generation_match=0, timeout=30)

        await asyncio.to_thread(write_marker)
    except Exception as error:
        # Provider errors may include request details. Do not return the raw
        # exception: service-account material must never enter a response.
        raise HTTPException(502, f"Cloud Storage upload failed ({type(error).__name__})") from error
    async with request.app.state.session_factory() as session:
        meta = await session.get(SecretMeta, CONNECTION_SECRET) or SecretMeta(name=CONNECTION_SECRET)
        meta.status = "valid"
        session.add(meta)
        await session.commit()
    return {"ok": True, "detail": "Upload permission verified. A small marker was saved."}


def _local_backups(directory: Path) -> list[dict]:
    if not directory.is_dir():
        return []
    result = []
    for path in directory.glob("ap-recovery-*.tar.age"):
        if not BACKUP_NAME.fullmatch(path.name) or not path.is_file():
            continue
        record = {"name": path.name, "bytes": path.stat().st_size,
                  "created_at": path.stat().st_mtime, "uploaded": False,
                  "gcs_uri": None}
        receipt = path.with_name(path.name + ".json")
        if receipt.is_file():
            try:
                saved = json.loads(receipt.read_text())
                if saved.get("name") == path.name:
                    record.update({"uploaded": True, "gcs_uri": saved.get("gcs_uri"),
                                   "sha256": saved.get("sha256"),
                                   "secret_count": saved.get("secret_count")})
            except (ValueError, OSError):
                pass
        result.append(record)
    return sorted(result, key=lambda item: item["name"], reverse=True)


@router.get("")
async def list_backups(request: Request):
    return _local_backups(Path(request.app.state.settings.backup_output_dir))


@router.get("/file/{name}")
async def download_backup(request: Request, name: str):
    if not BACKUP_NAME.fullmatch(name):
        raise HTTPException(404, "backup not found")
    path = Path(request.app.state.settings.backup_output_dir) / name
    if not path.is_file():
        raise HTTPException(404, "backup not found")
    return FileResponse(path, media_type="application/octet-stream", filename=name,
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/run")
async def start_backup(request: Request):
    """Start the same Helm-owned Job template the nightly CronJob runs."""
    if not (await get_backup_connection(request))["ready"]:
        raise HTTPException(422, "configure the backup connection first")
    from kubernetes import client, config

    settings = request.app.state.settings

    def create():
        config.load_incluster_config()
        api = client.BatchV1Api()
        cron = api.read_namespaced_cron_job(settings.backup_cronjob_name,
                                            settings.k8s_namespace)
        job = client.V1Job(
            metadata=client.V1ObjectMeta(generate_name=f"{settings.backup_cronjob_name}-manual-"),
            spec=cron.spec.job_template.spec)
        return api.create_namespaced_job(settings.k8s_namespace, job).metadata.name

    try:
        name = await asyncio.to_thread(create)
    except Exception as error:
        raise HTTPException(503, f"Could not start backup Job ({type(error).__name__})") from error
    return {"job": name}


@router.get("/jobs/{name}")
async def backup_job(request: Request, name: str):
    settings = request.app.state.settings
    if not name.startswith(settings.backup_cronjob_name + "-") or not re.fullmatch(r"[a-z0-9-]+", name):
        raise HTTPException(404, "backup Job not found")
    from kubernetes import client, config

    def read():
        config.load_incluster_config()
        return client.BatchV1Api().read_namespaced_job_status(name, settings.k8s_namespace)

    try:
        job = await asyncio.to_thread(read)
    except Exception as error:
        raise HTTPException(503, f"Could not read backup Job ({type(error).__name__})") from error
    status = job.status
    return {"job": name, "succeeded": status.succeeded or 0,
            "failed": status.failed or 0, "active": status.active or 0,
            "completed_at": status.completion_time}


@router.post("/import/inspect")
async def inspect_import(file: Annotated[UploadFile, File()],
                         identity: Annotated[str, Form()]):
    """Inspect a user-supplied encrypted archive; never keep the identity."""
    with tempfile.TemporaryDirectory(prefix="ap-backup-upload-") as work:
        encrypted = Path(work) / "upload.age"
        total = 0
        with encrypted.open("wb") as sink:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_IMPORT_BYTES:
                    raise HTTPException(413, "backup exceeds 512 MiB")
                sink.write(chunk)
        if not total:
            raise HTTPException(422, "backup file is empty")
        try:
            return await asyncio.to_thread(inspect_archive, encrypted, identity)
        except (ValueError, OSError, json.JSONDecodeError) as error:
            raise HTTPException(422, f"Cannot inspect backup: {error}") from error
