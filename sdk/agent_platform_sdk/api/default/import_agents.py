from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.agent_create_in import AgentCreateIn
from ...models.agent_import_result import AgentImportResult
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    *,
    body: list[AgentCreateIn],
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/agents/import",
    }

    _kwargs["json"] = []
    for body_item_data in body:
        body_item = body_item_data.to_dict()
        _kwargs["json"].append(body_item)

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[AgentImportResult] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = AgentImportResult.from_dict(response_200_item_data)

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
) -> Response[HTTPValidationError | list[AgentImportResult]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: list[AgentCreateIn],
) -> Response[HTTPValidationError | list[AgentImportResult]]:
    """Import Agents

     Idempotent bulk upsert of whole definitions — the one-shot migration
    path (docs/design/15) and the way a set of agents is seeded into a fresh
    cluster. Re-running the same payload is a no-op that logs nothing, so it is
    safe to run twice.

    All-or-nothing: every definition is validated before any of them is
    written, because a half-applied import leaves the platform in a state
    nobody described.

    Args:
        body (list[AgentCreateIn]):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[AgentImportResult]]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    body: list[AgentCreateIn],
) -> HTTPValidationError | list[AgentImportResult] | None:
    """Import Agents

     Idempotent bulk upsert of whole definitions — the one-shot migration
    path (docs/design/15) and the way a set of agents is seeded into a fresh
    cluster. Re-running the same payload is a no-op that logs nothing, so it is
    safe to run twice.

    All-or-nothing: every definition is validated before any of them is
    written, because a half-applied import leaves the platform in a state
    nobody described.

    Args:
        body (list[AgentCreateIn]):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[AgentImportResult]
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: list[AgentCreateIn],
) -> Response[HTTPValidationError | list[AgentImportResult]]:
    """Import Agents

     Idempotent bulk upsert of whole definitions — the one-shot migration
    path (docs/design/15) and the way a set of agents is seeded into a fresh
    cluster. Re-running the same payload is a no-op that logs nothing, so it is
    safe to run twice.

    All-or-nothing: every definition is validated before any of them is
    written, because a half-applied import leaves the platform in a state
    nobody described.

    Args:
        body (list[AgentCreateIn]):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[AgentImportResult]]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: list[AgentCreateIn],
) -> HTTPValidationError | list[AgentImportResult] | None:
    """Import Agents

     Idempotent bulk upsert of whole definitions — the one-shot migration
    path (docs/design/15) and the way a set of agents is seeded into a fresh
    cluster. Re-running the same payload is a no-op that logs nothing, so it is
    safe to run twice.

    All-or-nothing: every definition is validated before any of them is
    written, because a half-applied import leaves the platform in a state
    nobody described.

    Args:
        body (list[AgentCreateIn]):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[AgentImportResult]
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
