from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/tickets/events",
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
    """Ticket Stream

     The board, live: a `ticket` frame per change (the client upserts by id),
    a heartbeat so nothing in between decides an idle stream is a dead one, and
    an `overflow` marker for a reader that fell too far behind to be caught up
    by anything but a refetch.

    Kafka-fed only, unlike Relay's stream: a ticket change is a page of work
    rather than a line of conversation, and the reader is a board that refetches
    on reconnect, so the local hand-off Relay needs to stay live between pods
    would only buy a duplicate frame.

    An agent's stream is filtered to the rooms it belongs to, and re-asks that
    question on every quiet tick: membership can be taken away while a stream is
    open, and a socket that kept delivering afterwards is the one way an agent
    reads a room it was thrown out of.

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
    """Ticket Stream

     The board, live: a `ticket` frame per change (the client upserts by id),
    a heartbeat so nothing in between decides an idle stream is a dead one, and
    an `overflow` marker for a reader that fell too far behind to be caught up
    by anything but a refetch.

    Kafka-fed only, unlike Relay's stream: a ticket change is a page of work
    rather than a line of conversation, and the reader is a board that refetches
    on reconnect, so the local hand-off Relay needs to stay live between pods
    would only buy a duplicate frame.

    An agent's stream is filtered to the rooms it belongs to, and re-asks that
    question on every quiet tick: membership can be taken away while a stream is
    open, and a socket that kept delivering afterwards is the one way an agent
    reads a room it was thrown out of.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)
