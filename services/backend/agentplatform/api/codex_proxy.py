"""Credential persistence door used only by the in-cluster Codex broker."""
import json

from fastapi import APIRouter, HTTPException, Request
from agentplatform.api.quota import _authenticated
from agentplatform.db import SecretMeta
from agentplatform.secrets import CODEX_CREDENTIAL

router = APIRouter()
MAX_BYTES = 128 * 1024


async def _body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BYTES:
        raise HTTPException(413, "credential update is too large")
    value = bytearray()
    async for chunk in request.stream():
        value.extend(chunk)
        if len(value) > MAX_BYTES:
            raise HTTPException(413, "credential update is too large")
    return bytes(value)


def _auth_json(raw: bytes) -> str:
    try:
        envelope = json.loads(raw or b"{}")
        text = envelope["auth_json"]
        auth = json.loads(text)
        tokens = auth["tokens"]
        required = ("access_token", "refresh_token", "account_id")
        if (auth.get("auth_mode") != "chatgpt" or not isinstance(text, str)
                or any(not isinstance(tokens.get(k), str) or not tokens[k]
                       for k in required)):
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(422, "invalid Codex credential update") from None
    return text


@router.post("/api/internal/codex-auth", include_in_schema=False)
async def persist_codex_auth(request: Request):
    _authenticated(request)  # authenticate before reading a byte of the body
    text = _auth_json(await _body(request))
    await request.app.state.secret_store.set(CODEX_CREDENTIAL, {"auth.json": text})
    async with request.app.state.session_factory() as session:
        meta = await session.get(SecretMeta, CODEX_CREDENTIAL) or SecretMeta(
            name=CODEX_CREDENTIAL)
        meta.status = "valid"
        session.add(meta)
        await session.commit()
    return {"ok": True}
