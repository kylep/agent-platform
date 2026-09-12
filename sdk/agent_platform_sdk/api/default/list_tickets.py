from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.ticket_view import TicketView
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    channel: None | str | Unset = UNSET,
    state: None | str | Unset = UNSET,
    assignee: None | str | Unset = UNSET,
    label: None | str | Unset = UNSET,
    q: None | str | Unset = UNSET,
    mine: bool | Unset = False,
    limit: int | Unset = 500,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_channel: None | str | Unset
    if isinstance(channel, Unset):
        json_channel = UNSET
    else:
        json_channel = channel
    params["channel"] = json_channel

    json_state: None | str | Unset
    if isinstance(state, Unset):
        json_state = UNSET
    else:
        json_state = state
    params["state"] = json_state

    json_assignee: None | str | Unset
    if isinstance(assignee, Unset):
        json_assignee = UNSET
    else:
        json_assignee = assignee
    params["assignee"] = json_assignee

    json_label: None | str | Unset
    if isinstance(label, Unset):
        json_label = UNSET
    else:
        json_label = label
    params["label"] = json_label

    json_q: None | str | Unset
    if isinstance(q, Unset):
        json_q = UNSET
    else:
        json_q = q
    params["q"] = json_q

    params["mine"] = mine

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/tickets",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[TicketView] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = TicketView.from_dict(response_200_item_data)

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
) -> Response[HTTPValidationError | list[TicketView]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    channel: None | str | Unset = UNSET,
    state: None | str | Unset = UNSET,
    assignee: None | str | Unset = UNSET,
    label: None | str | Unset = UNSET,
    q: None | str | Unset = UNSET,
    mine: bool | Unset = False,
    limit: int | Unset = 500,
) -> Response[HTTPValidationError | list[TicketView]]:
    """List Tickets

     The board, filtered. Ordered by priority and then by activity, which is
    the order a column is read in: the most urgent thing that moved most
    recently is at the top.

    `q` is a substring match on title and body. Deliberately ILIKE rather than
    the tsvector Relay's search uses: a ticket title is a headline, not a
    document, and `weather` must match `weather-dedup`, which a stemmed index
    would not. The GIN index in the design is there if a body search ever needs
    it.

    Args:
        channel (None | str | Unset):
        state (None | str | Unset):
        assignee (None | str | Unset):
        label (None | str | Unset):
        q (None | str | Unset):
        mine (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 500.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[TicketView]]
    """

    kwargs = _get_kwargs(
        channel=channel,
        state=state,
        assignee=assignee,
        label=label,
        q=q,
        mine=mine,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    channel: None | str | Unset = UNSET,
    state: None | str | Unset = UNSET,
    assignee: None | str | Unset = UNSET,
    label: None | str | Unset = UNSET,
    q: None | str | Unset = UNSET,
    mine: bool | Unset = False,
    limit: int | Unset = 500,
) -> HTTPValidationError | list[TicketView] | None:
    """List Tickets

     The board, filtered. Ordered by priority and then by activity, which is
    the order a column is read in: the most urgent thing that moved most
    recently is at the top.

    `q` is a substring match on title and body. Deliberately ILIKE rather than
    the tsvector Relay's search uses: a ticket title is a headline, not a
    document, and `weather` must match `weather-dedup`, which a stemmed index
    would not. The GIN index in the design is there if a body search ever needs
    it.

    Args:
        channel (None | str | Unset):
        state (None | str | Unset):
        assignee (None | str | Unset):
        label (None | str | Unset):
        q (None | str | Unset):
        mine (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 500.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[TicketView]
    """

    return sync_detailed(
        client=client,
        channel=channel,
        state=state,
        assignee=assignee,
        label=label,
        q=q,
        mine=mine,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    channel: None | str | Unset = UNSET,
    state: None | str | Unset = UNSET,
    assignee: None | str | Unset = UNSET,
    label: None | str | Unset = UNSET,
    q: None | str | Unset = UNSET,
    mine: bool | Unset = False,
    limit: int | Unset = 500,
) -> Response[HTTPValidationError | list[TicketView]]:
    """List Tickets

     The board, filtered. Ordered by priority and then by activity, which is
    the order a column is read in: the most urgent thing that moved most
    recently is at the top.

    `q` is a substring match on title and body. Deliberately ILIKE rather than
    the tsvector Relay's search uses: a ticket title is a headline, not a
    document, and `weather` must match `weather-dedup`, which a stemmed index
    would not. The GIN index in the design is there if a body search ever needs
    it.

    Args:
        channel (None | str | Unset):
        state (None | str | Unset):
        assignee (None | str | Unset):
        label (None | str | Unset):
        q (None | str | Unset):
        mine (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 500.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[TicketView]]
    """

    kwargs = _get_kwargs(
        channel=channel,
        state=state,
        assignee=assignee,
        label=label,
        q=q,
        mine=mine,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    channel: None | str | Unset = UNSET,
    state: None | str | Unset = UNSET,
    assignee: None | str | Unset = UNSET,
    label: None | str | Unset = UNSET,
    q: None | str | Unset = UNSET,
    mine: bool | Unset = False,
    limit: int | Unset = 500,
) -> HTTPValidationError | list[TicketView] | None:
    """List Tickets

     The board, filtered. Ordered by priority and then by activity, which is
    the order a column is read in: the most urgent thing that moved most
    recently is at the top.

    `q` is a substring match on title and body. Deliberately ILIKE rather than
    the tsvector Relay's search uses: a ticket title is a headline, not a
    document, and `weather` must match `weather-dedup`, which a stemmed index
    would not. The GIN index in the design is there if a body search ever needs
    it.

    Args:
        channel (None | str | Unset):
        state (None | str | Unset):
        assignee (None | str | Unset):
        label (None | str | Unset):
        q (None | str | Unset):
        mine (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 500.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[TicketView]
    """

    return (
        await asyncio_detailed(
            client=client,
            channel=channel,
            state=state,
            assignee=assignee,
            label=label,
            q=q,
            mine=mine,
            limit=limit,
        )
    ).parsed
