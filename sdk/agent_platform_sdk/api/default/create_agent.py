from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.create_agent_in import CreateAgentIn
from ...models.edit_result import EditResult
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    *,
    body: CreateAgentIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/agents",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> EditResult | HTTPValidationError | None:
    if response.status_code == 201:
        response_201 = EditResult.from_dict(response.json())

        return response_201

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[EditResult | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: CreateAgentIn,
) -> Response[EditResult | HTTPValidationError]:
    """Create Agent

     Create a new agent from the wizard: render its manifest.yaml + agent.md
    and open a pull request (new files are always Tier 2 / review-gated).

    Args:
        body (CreateAgentIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EditResult | HTTPValidationError]
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
    body: CreateAgentIn,
) -> EditResult | HTTPValidationError | None:
    """Create Agent

     Create a new agent from the wizard: render its manifest.yaml + agent.md
    and open a pull request (new files are always Tier 2 / review-gated).

    Args:
        body (CreateAgentIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EditResult | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: CreateAgentIn,
) -> Response[EditResult | HTTPValidationError]:
    """Create Agent

     Create a new agent from the wizard: render its manifest.yaml + agent.md
    and open a pull request (new files are always Tier 2 / review-gated).

    Args:
        body (CreateAgentIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EditResult | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: CreateAgentIn,
) -> EditResult | HTTPValidationError | None:
    """Create Agent

     Create a new agent from the wizard: render its manifest.yaml + agent.md
    and open a pull request (new files are always Tier 2 / review-gated).

    Args:
        body (CreateAgentIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EditResult | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
