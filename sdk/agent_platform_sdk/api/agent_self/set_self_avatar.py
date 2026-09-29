from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.agent_image_in import AgentImageIn
from ...models.http_validation_error import HTTPValidationError
from ...models.self_profile_out import SelfProfileOut
from ...types import Response


def _get_kwargs(
    *,
    body: AgentImageIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/api/agent-self/avatar",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | SelfProfileOut | None:
    if response.status_code == 200:
        response_200 = SelfProfileOut.from_dict(response.json())

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
) -> Response[HTTPValidationError | SelfProfileOut]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: AgentImageIn,
) -> Response[HTTPValidationError | SelfProfileOut]:
    """Set Self Avatar

    Args:
        body (AgentImageIn): `PUT /api/agents/{name}/image` (docs/design/23): the picture's
            artifact,
            or null to take it off. The key is required so an empty body is a 422 and
            not a silent clear.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SelfProfileOut]
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
    body: AgentImageIn,
) -> HTTPValidationError | SelfProfileOut | None:
    """Set Self Avatar

    Args:
        body (AgentImageIn): `PUT /api/agents/{name}/image` (docs/design/23): the picture's
            artifact,
            or null to take it off. The key is required so an empty body is a 422 and
            not a silent clear.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SelfProfileOut
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: AgentImageIn,
) -> Response[HTTPValidationError | SelfProfileOut]:
    """Set Self Avatar

    Args:
        body (AgentImageIn): `PUT /api/agents/{name}/image` (docs/design/23): the picture's
            artifact,
            or null to take it off. The key is required so an empty body is a 422 and
            not a silent clear.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SelfProfileOut]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: AgentImageIn,
) -> HTTPValidationError | SelfProfileOut | None:
    """Set Self Avatar

    Args:
        body (AgentImageIn): `PUT /api/agents/{name}/image` (docs/design/23): the picture's
            artifact,
            or null to take it off. The key is required so an empty body is a 422 and
            not a silent clear.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SelfProfileOut
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
