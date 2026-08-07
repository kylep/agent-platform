from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.tool_audit_view import ToolAuditView
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    limit: int | Unset = 100,
    decision: None | str | Unset = UNSET,
    agent: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    json_decision: None | str | Unset
    if isinstance(decision, Unset):
        json_decision = UNSET
    else:
        json_decision = decision
    params["decision"] = json_decision

    json_agent: None | str | Unset
    if isinstance(agent, Unset):
        json_agent = UNSET
    else:
        json_agent = agent
    params["agent"] = json_agent

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/audit/tools",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[ToolAuditView] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = ToolAuditView.from_dict(response_200_item_data)

            response_200.append(response_200_item)

        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[HTTPValidationError | list[ToolAuditView]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
    decision: None | str | Unset = UNSET,
    agent: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | list[ToolAuditView]]:
    """List Tool Audit

     The broker's custom-tool audit trail (docs/design/13 E), newest first.
    Args are digests, never raw — safe to show.

    Args:
        limit (int | Unset):  Default: 100.
        decision (None | str | Unset):
        agent (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[ToolAuditView]]
    """

    kwargs = _get_kwargs(
        limit=limit,
        decision=decision,
        agent=agent,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
    decision: None | str | Unset = UNSET,
    agent: None | str | Unset = UNSET,
) -> HTTPValidationError | list[ToolAuditView] | None:
    """List Tool Audit

     The broker's custom-tool audit trail (docs/design/13 E), newest first.
    Args are digests, never raw — safe to show.

    Args:
        limit (int | Unset):  Default: 100.
        decision (None | str | Unset):
        agent (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[ToolAuditView]
    """

    return sync_detailed(
        client=client,
        limit=limit,
        decision=decision,
        agent=agent,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
    decision: None | str | Unset = UNSET,
    agent: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | list[ToolAuditView]]:
    """List Tool Audit

     The broker's custom-tool audit trail (docs/design/13 E), newest first.
    Args are digests, never raw — safe to show.

    Args:
        limit (int | Unset):  Default: 100.
        decision (None | str | Unset):
        agent (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[ToolAuditView]]
    """

    kwargs = _get_kwargs(
        limit=limit,
        decision=decision,
        agent=agent,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
    decision: None | str | Unset = UNSET,
    agent: None | str | Unset = UNSET,
) -> HTTPValidationError | list[ToolAuditView] | None:
    """List Tool Audit

     The broker's custom-tool audit trail (docs/design/13 E), newest first.
    Args are digests, never raw — safe to show.

    Args:
        limit (int | Unset):  Default: 100.
        decision (None | str | Unset):
        agent (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[ToolAuditView]
    """

    return (
        await asyncio_detailed(
            client=client,
            limit=limit,
            decision=decision,
            agent=agent,
        )
    ).parsed
