"""Publish generated site documents, refusing to overwrite unrelated documents.

Credentials stay in memory. Receipts contain only public IDs, hashes and versions.
Use a new output directory/slug prefix for a new edition; do not reuse receipts
to refresh documents that someone may have edited.
"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import uuid

import httpx


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def publish(client, directory, receipts_path):
    manifest_bytes = (directory / "manifest.json").read_bytes()
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    manifest = json.loads(manifest_bytes)
    receipts = json.loads(receipts_path.read_text()) if receipts_path.exists() else {
        "manifestSha256": digest, "baseUrl": str(client.base_url),
        "run": uuid.uuid4().hex, "documents": {}, "assets": {},
    }
    if receipts["manifestSha256"] != digest or receipts["baseUrl"] != str(client.base_url):
        raise ValueError("Receipt belongs to another capture or server; use a new edition")
    entries = [*manifest["documents"], manifest["index"]]
    documents = {}
    # Validate all inputs and check every destination before the first write.
    for item in entries:
        slug = item["slug"]
        content = (directory / item["file"]).read_bytes()
        if len(content) != item["bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError(f"Document changed after conversion: {slug}")
        documents[slug] = json.loads(content)
        existing = client.get(f"/api/ui-designer/docs/{slug}", params={"depth": 0})
        saved = receipts["documents"].get(slug)
        if existing.status_code == 404 and saved and saved.get("createdVersion"):
            raise ValueError(f"Published document is missing: {slug}")
        if existing.status_code != 404:
            existing.raise_for_status()
            if saved is None:
                raise ValueError(f"Refusing to overwrite existing document: {slug}")
            if saved.get("id") and existing.json()["id"] != saved["id"]:
                raise ValueError(f"Document was replaced since publication: {slug}")
            if saved.get("published") and existing.json()["version"] != saved["version"]:
                raise ValueError(f"Document was edited since publication: {slug}")
    for sha, item in manifest["assets"].items():
        if hashlib.sha256((directory / item["file"]).read_bytes()).hexdigest() != sha:
            raise ValueError(f"Asset changed after conversion: {sha}")
    save(receipts_path, receipts)

    for item in entries:
        slug = item["slug"]
        saved = receipts["documents"].setdefault(slug, {})
        if saved.get("published"):
            print(f"Already published {slug} v{saved['version']}", flush=True)
            continue
        # Save intent before issuing an idempotent create: a lost response can
        # be retried safely and never adopts an unrelated existing document.
        save(receipts_path, receipts)
        response = client.post("/api/ui-designer/docs", json={
            "slug": slug, "title": item["title"],
            "description": "Editable desktop site reference, captured in dark mode with representative fixture data. "
                           "Pages are design snapshots; application services and controls are not live. "
                           "Source and refresh instructions: services/web/design-capture/README.md.",
            "request_id": f"site-{receipts['run']}-{slug}-create",
            "reason": "Add editable Agent Platform site reference",
        })
        response.raise_for_status()
        created_version = response.json()["version"]
        current = client.get(f"/api/ui-designer/docs/{slug}", params={"depth": 0})
        current.raise_for_status()
        if saved.get("id") and current.json()["id"] != saved["id"]:
            raise ValueError(f"Document was replaced since publication: {slug}")
        saved["id"] = current.json()["id"]
        saved["createdVersion"] = created_version
        save(receipts_path, receipts)
        for sha in item["assets"]:
            asset = manifest["assets"][sha]
            if sha not in receipts["assets"]:
                response = client.post("/api/artifacts", json={
                    "name": f"Site reference {sha[:12]}", "mime": asset["mime"],
                    "content_b64": base64.b64encode((directory / asset["file"]).read_bytes()).decode(),
                    "source": "upload", "tags": ["site-reference"],
                })
                response.raise_for_status()
                receipts["assets"][sha] = response.json()["id"]
                save(receipts_path, receipts)
            response = client.post(f"/api/ui-designer/docs/{slug}/place", json={
                "artifact_id": receipts["assets"][sha],
            })
            response.raise_for_status()
            if response.json()["sha256"] != sha:
                raise ValueError(f"Server changed asset content: {sha}")
        response = client.post(f"/api/ui-designer/docs/{slug}/edit", json={
            "expected_version": created_version,
            "ops": [{"op": "set_doc", "doc": documents[slug]}],
            "request_id": f"site-{receipts['run']}-{slug}-content",
            "reason": f"Import {len(item['routes'])} editable site screens",
        })
        response.raise_for_status()
        current = client.get(f"/api/ui-designer/docs/{slug}", params={"depth": 0})
        current.raise_for_status()
        if current.json()["id"] != saved["id"] or current.json()["version"] != response.json()["version"]:
            raise ValueError(f"Document changed while publishing: {slug}")
        saved.update(published=True, version=response.json()["version"],
                     warnings=response.json().get("warnings", []))
        save(receipts_path, receipts)
        print(f"Published {slug} v{saved['version']} ({item['pages']} pages)", flush=True)
    return receipts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--receipts", type=Path)
    args = parser.parse_args()
    receipts = args.receipts or args.documents / "publish-receipts.json"
    password = os.environ.get("AGENT_PLATFORM_ADMIN")
    token = os.environ.get("AP_MCP_TOKEN")
    if not password and not token:
        parser.error("Set AGENT_PLATFORM_ADMIN or AP_MCP_TOKEN in the environment")
    with httpx.Client(base_url=args.base_url, timeout=120, follow_redirects=False) as client:
        if password:
            client.post("/api/login", json={"principal": "admin", "password": password}).raise_for_status()
        else:
            client.headers["Authorization"] = f"Bearer {token}"
        publish(client, args.documents, receipts)


if __name__ == "__main__":
    main()
