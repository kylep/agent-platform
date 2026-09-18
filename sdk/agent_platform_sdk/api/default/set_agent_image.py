from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.agent_def_out import AgentDefOut
from ...models.agent_image_in import AgentImageIn
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    name: str,
    *,
    body: AgentImageIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/api/agents/{name}/image".format(
            name=quote(str(name), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AgentDefOut | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = AgentDefOut.from_dict(response.json())

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
) -> Response[AgentDefOut | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    name: str,
    *,
    client: AuthenticatedClient | Client,
    body: AgentImageIn,
) -> Response[AgentDefOut | HTTPValidationError]:
    """Set Agent Image

     Set or clear the agent's picture. The artifact must be a live image:
    a document would render nothing, and a deleted one is a 404 like every
    other read of it. Publishes `agent_image` on `artifacts.events` — with
    `artifact: null` for a clear — so the Studio and the #art feed see the
    face change as they see the picture land.

    Args:
        name (str):
        body (AgentImageIn): `PUT /api/agents/{name}/image` (docs/design/23): the picture's
            artifact,
            or null to take it off. The key is required so an empty body is a 422 and
            not a silent clear.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentDefOut | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        name=name,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    name: str,
    *,
    client: AuthenticatedClient | Client,
    body: AgentImageIn,
) -> AgentDefOut | HTTPValidationError | None:
    """Set Agent Image

     Set or clear the agent's picture. The artifact must be a live image:
    a document would render nothing, and a deleted one is a 404 like every
    other read of it. Publishes `agent_image` on `artifacts.events` — with
    `artifact: null` for a clear — so the Studio and the #art feed see the
    face change as they see the picture land.

    Args:
        name (str):
        body (AgentImageIn): `PUT /api/agents/{name}/image` (docs/design/23): the picture's
            artifact,
            or null to take it off. The key is required so an empty body is a 422 and
            not a silent clear.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentDefOut | HTTPValidationError
    """

    return sync_detailed(
        name=name,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    name: str,
    *,
    client: AuthenticatedClient | Client,
    body: AgentImageIn,
) -> Response[AgentDefOut | HTTPValidationError]:
    """Set Agent Image

     Set or clear the agent's picture. The artifact must be a live image:
    a document would render nothing, and a deleted one is a 404 like every
    other read of it. Publishes `agent_image` on `artifacts.events` — with
    `artifact: null` for a clear — so the Studio and the #art feed see the
    face change as they see the picture land.

    Args:
        name (str):
        body (AgentImageIn): `PUT /api/agents/{name}/image` (docs/design/23): the picture's
            artifact,
            or null to take it off. The key is required so an empty body is a 422 and
            not a silent clear.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentDefOut | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        name=name,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    name: str,
    *,
    client: AuthenticatedClient | Client,
    body: AgentImageIn,
) -> AgentDefOut | HTTPValidationError | None:
    """Set Agent Image

     Set or clear the agent's picture. The artifact must be a live image:
    a document would render nothing, and a deleted one is a 404 like every
    other read of it. Publishes `agent_image` on `artifacts.events` — with
    `artifact: null` for a clear — so the Studio and the #art feed see the
    face change as they see the picture land.

    Args:
        name (str):
        body (AgentImageIn): `PUT /api/agents/{name}/image` (docs/design/23): the picture's
            artifact,
            or null to take it off. The key is required so an empty body is a 422 and
            not a silent clear.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentDefOut | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            name=name,
            client=client,
            body=body,
        )
    ).parsed
