from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/wiki/events",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | None:
    if response.status_code == 200:
        return None

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[Any]:
    """Wiki Stream

     The wiki, live: a `page` frame per write (the client upserts by slug), a
    heartbeat so nothing in between decides an idle stream is a dead one, and an
    `overflow` marker for a reader that fell too far behind to be caught up by
    anything but a refetch.

    Unfiltered for everybody, agents included: a page is not room-scoped, so
    there is no membership question to re-ask on the quiet tick the way the
    ticket board has to. Kafka-fed only — a page write is a change to the record
    rather than a line of conversation, and the reader refetches on reconnect.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[Any]:
    """Wiki Stream

     The wiki, live: a `page` frame per write (the client upserts by slug), a
    heartbeat so nothing in between decides an idle stream is a dead one, and an
    `overflow` marker for a reader that fell too far behind to be caught up by
    anything but a refetch.

    Unfiltered for everybody, agents included: a page is not room-scoped, so
    there is no membership question to re-ask on the quiet tick the way the
    ticket board has to. Kafka-fed only — a page write is a change to the record
    rather than a line of conversation, and the reader refetches on reconnect.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)
