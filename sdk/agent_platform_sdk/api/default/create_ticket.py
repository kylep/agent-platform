from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.ticket_in import TicketIn
from ...models.ticket_view import TicketView
from ...types import Response


def _get_kwargs(
    *,
    body: TicketIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/tickets",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | TicketView | None:
    if response.status_code == 201:
        response_201 = TicketView.from_dict(response.json())

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
) -> Response[HTTPValidationError | TicketView]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: TicketIn,
) -> Response[HTTPValidationError | TicketView]:
    """Create Ticket

     Open a ticket. The reporter is the caller, from the token — there is no
    reporter field on the wire, so a prompt-injected agent gains nothing by
    asking to file as somebody else.

    An agent's create is budgeted (`tickets_agent_creates_per_hour`), and the
    refusal is answered twice over: a 429 the agent reads, and one line in the
    project channel per hour so the humans watching the room can see that an
    agent is looping without the board filling up with the evidence.

    Args:
        body (TicketIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | TicketView]
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
    body: TicketIn,
) -> HTTPValidationError | TicketView | None:
    """Create Ticket

     Open a ticket. The reporter is the caller, from the token — there is no
    reporter field on the wire, so a prompt-injected agent gains nothing by
    asking to file as somebody else.

    An agent's create is budgeted (`tickets_agent_creates_per_hour`), and the
    refusal is answered twice over: a 429 the agent reads, and one line in the
    project channel per hour so the humans watching the room can see that an
    agent is looping without the board filling up with the evidence.

    Args:
        body (TicketIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | TicketView
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: TicketIn,
) -> Response[HTTPValidationError | TicketView]:
    """Create Ticket

     Open a ticket. The reporter is the caller, from the token — there is no
    reporter field on the wire, so a prompt-injected agent gains nothing by
    asking to file as somebody else.

    An agent's create is budgeted (`tickets_agent_creates_per_hour`), and the
    refusal is answered twice over: a 429 the agent reads, and one line in the
    project channel per hour so the humans watching the room can see that an
    agent is looping without the board filling up with the evidence.

    Args:
        body (TicketIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | TicketView]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: TicketIn,
) -> HTTPValidationError | TicketView | None:
    """Create Ticket

     Open a ticket. The reporter is the caller, from the token — there is no
    reporter field on the wire, so a prompt-injected agent gains nothing by
    asking to file as somebody else.

    An agent's create is budgeted (`tickets_agent_creates_per_hour`), and the
    refusal is answered twice over: a 429 the agent reads, and one line in the
    project channel per hour so the humans watching the room can see that an
    agent is looping without the board filling up with the evidence.

    Args:
        body (TicketIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | TicketView
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
