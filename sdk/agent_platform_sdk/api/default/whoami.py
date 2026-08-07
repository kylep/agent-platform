from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.who_am_i import WhoAmI
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/whoami",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> WhoAmI | None:
    if response.status_code == 200:
        response_200 = WhoAmI.from_dict(response.json())

        return response_200

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[WhoAmI]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[WhoAmI]:
    """Whoami

     Verified caller identity (docs/design/12): the MCP broker calls this
    with a forwarded bearer to resolve WHO is invoking a custom tool — agent,
    run, and the mcp__platform__* tools that agent's definition declares. The
    broker enforces tool grants from this answer, so identity is derived from
    the token, never from anything the model says. Accepts every authenticated
    role including `tools` (whose keys can reach nothing else).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[WhoAmI]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> WhoAmI | None:
    """Whoami

     Verified caller identity (docs/design/12): the MCP broker calls this
    with a forwarded bearer to resolve WHO is invoking a custom tool — agent,
    run, and the mcp__platform__* tools that agent's definition declares. The
    broker enforces tool grants from this answer, so identity is derived from
    the token, never from anything the model says. Accepts every authenticated
    role including `tools` (whose keys can reach nothing else).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        WhoAmI
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[WhoAmI]:
    """Whoami

     Verified caller identity (docs/design/12): the MCP broker calls this
    with a forwarded bearer to resolve WHO is invoking a custom tool — agent,
    run, and the mcp__platform__* tools that agent's definition declares. The
    broker enforces tool grants from this answer, so identity is derived from
    the token, never from anything the model says. Accepts every authenticated
    role including `tools` (whose keys can reach nothing else).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[WhoAmI]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> WhoAmI | None:
    """Whoami

     Verified caller identity (docs/design/12): the MCP broker calls this
    with a forwarded bearer to resolve WHO is invoking a custom tool — agent,
    run, and the mcp__platform__* tools that agent's definition declares. The
    broker enforces tool grants from this answer, so identity is derived from
    the token, never from anything the model says. Accepts every authenticated
    role including `tools` (whose keys can reach nothing else).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        WhoAmI
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
