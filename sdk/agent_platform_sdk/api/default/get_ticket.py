from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.ticket_detail import TicketDetail
from ...types import Response


def _get_kwargs(
    key: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/tickets/{key}".format(
            key=quote(str(key), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | TicketDetail | None:
    if response.status_code == 200:
        response_200 = TicketDetail.from_dict(response.json())

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
) -> Response[HTTPValidationError | TicketDetail]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    key: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | TicketDetail]:
    """Get Ticket

     One ticket: the row, its structured history, the id of the thread its
    discussion lives in, the runs it produced, and whoever is working on it
    right now. The messages themselves are Relay's to serve — the client asks
    for the thread under `root_message_id`, so a ticket's conversation has one
    endpoint and not two.

    Args:
        key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | TicketDetail]
    """

    kwargs = _get_kwargs(
        key=key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    key: str,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | TicketDetail | None:
    """Get Ticket

     One ticket: the row, its structured history, the id of the thread its
    discussion lives in, the runs it produced, and whoever is working on it
    right now. The messages themselves are Relay's to serve — the client asks
    for the thread under `root_message_id`, so a ticket's conversation has one
    endpoint and not two.

    Args:
        key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | TicketDetail
    """

    return sync_detailed(
        key=key,
        client=client,
    ).parsed


async def asyncio_detailed(
    key: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | TicketDetail]:
    """Get Ticket

     One ticket: the row, its structured history, the id of the thread its
    discussion lives in, the runs it produced, and whoever is working on it
    right now. The messages themselves are Relay's to serve — the client asks
    for the thread under `root_message_id`, so a ticket's conversation has one
    endpoint and not two.

    Args:
        key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | TicketDetail]
    """

    kwargs = _get_kwargs(
        key=key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    key: str,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | TicketDetail | None:
    """Get Ticket

     One ticket: the row, its structured history, the id of the thread its
    discussion lives in, the runs it produced, and whoever is working on it
    right now. The messages themselves are Relay's to serve — the client asks
    for the thread under `root_message_id`, so a ticket's conversation has one
    endpoint and not two.

    Args:
        key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | TicketDetail
    """

    return (
        await asyncio_detailed(
            key=key,
            client=client,
        )
    ).parsed
