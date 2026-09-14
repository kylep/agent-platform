from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.quota import Quota
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/quota/refresh",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Quota | None:
    if response.status_code == 200:
        response_200 = Quota.from_dict(response.json())

        return response_200

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Quota]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[Quota]:
    """Refresh Quota

     Ask on purpose. Coalesced and rate-limited platform-wide, so this is
    safe to put behind a button and behind a tool.

    Everything happens under the lock, the short-circuit included: a caller
    that arrives during a probe waits for it and then finds the fresh snapshot
    it wrote, which is the same answer it would have got from its own probe and
    one fewer request to Anthropic.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Quota]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> Quota | None:
    """Refresh Quota

     Ask on purpose. Coalesced and rate-limited platform-wide, so this is
    safe to put behind a button and behind a tool.

    Everything happens under the lock, the short-circuit included: a caller
    that arrives during a probe waits for it and then finds the fresh snapshot
    it wrote, which is the same answer it would have got from its own probe and
    one fewer request to Anthropic.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Quota
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[Quota]:
    """Refresh Quota

     Ask on purpose. Coalesced and rate-limited platform-wide, so this is
    safe to put behind a button and behind a tool.

    Everything happens under the lock, the short-circuit included: a caller
    that arrives during a probe waits for it and then finds the fresh snapshot
    it wrote, which is the same answer it would have got from its own probe and
    one fewer request to Anthropic.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Quota]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> Quota | None:
    """Refresh Quota

     Ask on purpose. Coalesced and rate-limited platform-wide, so this is
    safe to put behind a button and behind a tool.

    Everything happens under the lock, the short-circuit included: a caller
    that arrives during a probe waits for it and then finds the fresh snapshot
    it wrote, which is the same answer it would have got from its own probe and
    one fewer request to Anthropic.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Quota
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
