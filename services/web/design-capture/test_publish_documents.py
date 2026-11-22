import base64
import hashlib
import json

import httpx
import pytest

from publish_documents import publish


def edition(tmp_path):
    content = b"local fixture artwork"
    sha = hashlib.sha256(content).hexdigest()
    (tmp_path / "art.png").write_bytes(content)

    def entry(slug, assets):
        filename = f"{slug}.json"
        document = json.dumps({"title": slug, "pages": []}).encode()
        (tmp_path / filename).write_bytes(document)
        return {
            "slug": slug, "title": slug, "file": filename, "bytes": len(document),
            "sha256": hashlib.sha256(document).hexdigest(),
            "assets": assets, "routes": [{"path": "/"}], "pages": 1,
        }

    manifest = {
        "documents": [entry("site-core", [sha])],
        "index": entry("site-index", []),
        "assets": {sha: {"file": "art.png", "mime": "image/png"}},
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path / "receipts.json", sha


class Designer:
    def __init__(self):
        self.documents = {}
        self.artifacts = {}
        self.placements = set()
        self.replays = {}
        self.writes = []
        self.lose_edit_response = False
        self.lose_create_response = False

    def send(self, request):
        path = request.url.path
        if path == "/api/artifacts" and request.method == "GET":
            return httpx.Response(200, json=list(self.artifacts.values()))
        if request.method == "GET":
            slug = path.rsplit("/", 1)[-1]
            row = self.documents.get(slug)
            return httpx.Response(200, json=row) if row else httpx.Response(404)

        body = json.loads(request.content)
        self.writes.append((path, body))
        request_id = body.get("request_id")
        if request_id in self.replays:
            previous_body, result = self.replays[request_id]
            assert body == previous_body
            return httpx.Response(200, json={**result, "replayed": True})

        if path == "/api/ui-designer/docs":
            slug = body["slug"]
            if slug in self.documents:
                return httpx.Response(409)
            result = {"slug": slug, "version": 1}
            self.documents[slug] = {"id": slug, "version": 1}
            self.replays[request_id] = body, result
            if self.lose_create_response:
                self.lose_create_response = False
                raise httpx.ReadError("create response lost", request=request)
        elif path == "/api/artifacts":
            sha = hashlib.sha256(base64.b64decode(body["content_b64"])).hexdigest()
            result = {"id": f"artifact-{len(self.artifacts) + 1}", "sha256": sha, "name": body["name"]}
            self.artifacts[result["id"]] = result
        else:
            slug, operation = path.split("/")[-2:]
            if operation == "place":
                sha = self.artifacts[body["artifact_id"]]["sha256"]
                self.placements.add((slug, sha))
                result = {"sha256": sha}
            else:
                assert operation == "edit"
                assert body["expected_version"] == self.documents[slug]["version"]
                if slug == "site-core":
                    assert {doc_slug for doc_slug, _ in self.placements} == {slug}
                result = {"slug": slug, "version": body["expected_version"] + 1}
                self.documents[slug]["version"] = result["version"]
                self.replays[request_id] = body, result
                if self.lose_edit_response:
                    self.lose_edit_response = False
                    raise httpx.ReadError("edit response lost", request=request)
        return httpx.Response(200, json=result)

    def client(self):
        return httpx.Client(base_url="https://designer.example", transport=httpx.MockTransport(self.send))


def test_preflight_refuses_existing_destination_without_writing(tmp_path):
    receipts_path, _ = edition(tmp_path)
    server = Designer()
    server.documents["site-index"] = {"id": "existing", "version": 7}
    with server.client() as client, pytest.raises(ValueError, match="Refusing to overwrite.*site-index"):
        publish(client, tmp_path, receipts_path)
    assert server.writes == []
    assert not receipts_path.exists()


@pytest.mark.parametrize("lost_response", ["create", "edit"])
def test_resume_after_lost_response_does_not_duplicate_changes(tmp_path, lost_response):
    receipts_path, sha = edition(tmp_path)
    server = Designer()
    setattr(server, f"lose_{lost_response}_response", True)
    with server.client() as client:
        with pytest.raises(httpx.ReadError, match=f"{lost_response} response lost"):
            publish(client, tmp_path, receipts_path)
        receipts = publish(client, tmp_path, receipts_path)
        writes = server.writes.copy()
        assert publish(client, tmp_path, receipts_path) == receipts

    assert server.writes == writes
    assert len(server.artifacts) == 1
    assert server.placements == {("site-core", sha)}
    assert {slug: item["version"] for slug, item in receipts["documents"].items()} == {
        "site-core": 2, "site-index": 2,
    }


def test_preflight_refuses_to_refresh_an_edited_document(tmp_path):
    receipts_path, _ = edition(tmp_path)
    server = Designer()
    with server.client() as client:
        publish(client, tmp_path, receipts_path)
        server.documents["site-index"]["version"] += 1
        writes = server.writes.copy()
        with pytest.raises(ValueError, match="edited since publication.*site-index"):
            publish(client, tmp_path, receipts_path)
    assert server.writes == writes


def test_upload_and_place_assets_before_replacing_document(tmp_path):
    receipts_path, sha = edition(tmp_path)
    server = Designer()
    with server.client() as client:
        publish(client, tmp_path, receipts_path)
    paths = [path for path, body in server.writes]
    upload = next(body for path, body in server.writes if path == "/api/artifacts")
    assert upload["source"] == "upload"  # "derived" requires an existing artifact parent.
    assert paths.index("/api/artifacts") < paths.index("/api/ui-designer/docs/site-core/place")
    assert paths.index("/api/ui-designer/docs/site-core/place") < paths.index("/api/ui-designer/docs/site-core/edit")
    assert server.placements == {("site-core", sha)}


def test_preflight_detects_replaced_asset_before_writing(tmp_path):
    receipts_path, _ = edition(tmp_path)
    (tmp_path / "art.png").write_bytes(b"changed asset")
    server = Designer()
    with server.client() as client, pytest.raises(ValueError, match="Asset changed"):
        publish(client, tmp_path, receipts_path)
    assert server.writes == []


def test_preflight_refuses_to_skip_a_missing_published_document(tmp_path):
    receipts_path, _ = edition(tmp_path)
    server = Designer()
    with server.client() as client:
        publish(client, tmp_path, receipts_path)
        del server.documents["site-index"]
        writes = server.writes.copy()
        with pytest.raises(ValueError, match="site-index"):
            publish(client, tmp_path, receipts_path)
    assert server.writes == writes


def test_lost_edit_receipt_does_not_accept_a_later_version(tmp_path):
    receipts_path, _ = edition(tmp_path)
    server = Designer()
    server.lose_edit_response = True
    with server.client() as client:
        with pytest.raises(httpx.ReadError, match="edit response lost"):
            publish(client, tmp_path, receipts_path)
        server.documents["site-core"]["version"] += 1
        with pytest.raises(ValueError, match="site-core"):
            publish(client, tmp_path, receipts_path)
    assert not json.loads(receipts_path.read_text())["documents"]["site-core"].get("published")
    assert "site-index" not in server.documents


def test_preflight_detects_same_size_document_replacement(tmp_path):
    receipts_path, _ = edition(tmp_path)
    path = tmp_path / "site-core.json"
    path.write_bytes(path.read_bytes().replace(b"site-core", b"site-evil"))
    server = Designer()
    with server.client() as client, pytest.raises(ValueError, match="Document changed.*site-core"):
        publish(client, tmp_path, receipts_path)
    assert server.writes == []


def test_preflight_refuses_a_replacement_with_the_same_version(tmp_path):
    receipts_path, _ = edition(tmp_path)
    server = Designer()
    with server.client() as client:
        publish(client, tmp_path, receipts_path)
        server.documents["site-index"]["id"] = "different-document"
        writes = server.writes.copy()
        with pytest.raises(ValueError, match="replaced since publication.*site-index"):
            publish(client, tmp_path, receipts_path)
    assert server.writes == writes
