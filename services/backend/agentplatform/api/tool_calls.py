"""Tool-call credential exchange (docs/design/39, "Tool-call credentials").

Two machine routes, answered only to the MCP broker's workload identity (its
projected ServiceAccount token as the bearer):

- `POST /api/tool-calls` exchanges the broker's caller (bearer and run JWT,
  exactly what the broker forwards to `/api/whoami`) for a credential bound to
  one call of one tool, which the broker hands to the executor;
- `DELETE /api/tool-calls/{jti}` revokes it when the call returns.

Neither is in the OpenAPI schema: no SDK user can hold the broker's identity.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from agentplatform.api import auth
from agentplatform.appdata import credentials as tc

router = APIRouter()

_ACTION = re.compile(r"^[a-z0-9_.-]{0,64}$")


class CallerIn(BaseModel):
    # The caller's `Authorization` header value and `X-AP-Run-Token`, as
    # the broker received them.
    authorization: str = Field(max_length=8192)
    run_token: str = Field(default="", max_length=8192)


class MintIn(BaseModel):
    caller: CallerIn
    tool: str = Field(max_length=64)
    action: str = Field(default="", max_length=64)


async def _require_broker(request: Request) -> None:
    header = request.headers.get("authorization", "")
    token = header[len("Bearer "):].strip() if header.startswith("Bearer ") else ""
    if token.count(".") != 2:
        raise HTTPException(401)
    sa_name = await auth.workload_sa(request, token)
    if sa_name is None:
        raise HTTPException(401)
    if sa_name != request.app.state.settings.broker_service_account:
        raise HTTPException(403, "only the MCP broker exchanges tool-call credentials")


@router.post("/api/tool-calls", include_in_schema=False)
async def mint(request: Request, body: MintIn):
    await _require_broker(request)
    if not _ACTION.match(body.action):
        raise HTTPException(422, "action must be a short lowercase name")
    caller = body.caller.authorization
    if not caller.startswith("Bearer "):
        raise HTTPException(401, "the caller presented no bearer")
    ident = await auth.authenticate_bearer(request, caller[len("Bearer "):].strip(),
                                           body.caller.run_token)
    if ident is None:
        raise HTTPException(401, "the caller's credentials did not verify")
    agent = getattr(request.state, "api_key_agent", None)
    run_id = getattr(request.state, "api_key_run_id", None)
    # No run, no App access: an admin API key is a person's credential, and
    # a call credential acts as an agent inside one of its runs.
    if not agent or not run_id:
        raise HTTPException(403, "no run: tool-call credentials are minted only for an "
                            "agent's run")

    st = request.app.state
    st.tool_registry.reload()
    info = st.tool_registry.get(body.tool)
    manifest = info.manifest if info else None
    if manifest is None or manifest.internal:
        raise HTTPException(404, f"no callable tool {body.tool!r}")
    if manifest.app_access is None:
        raise HTTPException(403, f"{body.tool} declares no app_access")
    # The broker checked the grant too; the credential must not depend on it.
    frozen = getattr(request.state, "frozen_tools", None)
    if frozen is None:
        await st.agent_store.reload()
        live = st.agent_store.get(agent)
        frozen = live.platform_tools if live else []
    if manifest.mcp_name not in frozen:
        raise HTTPException(403, f"agent {agent} does not hold {body.tool}")

    keys = await tc.keypair(st)
    call_id = uuid.uuid4().hex
    async with st.session_factory() as session:
        scope = await tc.compute_app_scope(session, agent=agent, tool=body.tool,
                                           app_access=manifest.app_access)
        token, claims = tc.mint_tool_call(
            keys["private_key"], call_id=call_id, run_id=run_id, agent=agent,
            tool=body.tool, action=body.action, app_scope=scope,
            cnf_sa=st.settings.tool_executor_service_account,
            ttl_seconds=manifest.timeout_seconds + tc.EXP_SLACK_SECONDS)
        await tc.record(session, claims)
        await session.commit()
    return {"credential": token, "call_id": call_id, "jti": claims["jti"],
            "expires_at": datetime.fromtimestamp(claims["exp"], timezone.utc).isoformat(),
            "app_scope": scope}


@router.delete("/api/tool-calls/{jti}", include_in_schema=False)
async def revoke(request: Request, jti: str):
    await _require_broker(request)
    async with request.app.state.session_factory() as session:
        found = await tc.revoke(session, jti)
        await session.commit()
    if not found:
        raise HTTPException(404)
    return {"ok": True}
