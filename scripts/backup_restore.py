#!/usr/bin/env python3
"""Offline recovery helper for encrypted Agent Platform archives.

This deliberately does not drop a database or apply Kubernetes changes. It
verifies/decrypts a backup and emits a safe Secret manifest for the documented
fresh-cluster restore. Run it from a trusted machine with age installed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "backend"))
from agentplatform.backup_export import inspect_archive

MEMBERS = {"manifest.json", "database.sql.gz", "secrets.json"}
GENERATED_EXACT = {"ap-internal", "ap-kafka-kraft", "ap-kafka-user-passwords",
                   "ap-postgresql", "qa-web-login", "run-jwt-key"}
GENERATED_PREFIXES = ("app-", "tool-")


def extract(encrypted: Path, key: Path, output: Path) -> None:
    if output.exists() and any(output.iterdir()):
        raise ValueError("output directory must be empty")
    inspect_archive(encrypted, key.read_text())
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(output, 0o700)
    process = subprocess.Popen(["age", "-d", "-i", str(key), str(encrypted)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.stdout is not None
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                if member.name not in MEMBERS or not member.isfile():
                    raise ValueError("archive contains unexpected members")
                source = archive.extractfile(member)
                assert source is not None
                with (output / member.name).open("xb") as target:
                    os.chmod(output / member.name, 0o600)
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
        process.stdout.close()
        _, error = process.communicate()
        if process.returncode:
            raise ValueError(f"decryption failed: {error.decode(errors='replace')[:180]}")
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait()
        for name in MEMBERS:
            (output / name).unlink(missing_ok=True)
        raise


def secret_manifest(secrets_file: Path) -> dict:
    records = json.loads(secrets_file.read_text())
    if not isinstance(records, list):
        raise TypeError("invalid Secret list")
    items = []
    for item in records:
        name = item["name"]
        if name in GENERATED_EXACT or name.startswith(GENERATED_PREFIXES):
            continue
        if item.get("type") != "Opaque":
            continue
        items.append({"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
                      "metadata": {"name": name}, "data": item["data"]})
    return {"apiVersion": "v1", "kind": "List", "items": items}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "extract"):
        command = commands.add_parser(name)
        command.add_argument("archive", type=Path)
        command.add_argument("--identity", type=Path, required=True)
        if name == "extract":
            command.add_argument("--output", type=Path, required=True)
    manifest = commands.add_parser("secrets-manifest")
    manifest.add_argument("secrets_file", type=Path)
    args = parser.parse_args()
    if args.command == "inspect":
        print(json.dumps(inspect_archive(args.archive, args.identity.read_text()), indent=2))
    elif args.command == "extract":
        extract(args.archive, args.identity, args.output)
        print(f"Verified recovery files extracted to {args.output}")
    else:
        print(json.dumps(secret_manifest(args.secrets_file)))


if __name__ == "__main__":
    main()
