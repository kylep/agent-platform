from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.ticket_patch import TicketPatch
from ...models.ticket_view import TicketView
from ...types import Response


def _get_kwargs(
    key: str,
    *,
    body: TicketPatch,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/api/tickets/{key}".format(
            key=quote(str(key), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | TicketView | None:
    if response.status_code == 200:
        response_200 = TicketView.from_dict(response.json())

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
) -> Response[HTTPValidationError | TicketView]:
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
    body: TicketPatch,
) -> Response[HTTPValidationError | TicketView]:
    """Update Ticket

     Edit the fields. Only what the caller actually sent is touched — the
    unset fields are not passed to the store at all — so clearing a due date and
    leaving it alone are different requests rather than the same null.

    Args:
        key (str):
        body (TicketPatch): The editable fields. Every one is nullable AND defaulted, so "clear
            the
            due date" and "leave it alone" are told apart by which keys the caller
            actually sent (`model_fields_set`), not by the value.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | TicketView]
    """

    kwargs = _get_kwargs(
        key=key,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    key: str,
    *,
    client: AuthenticatedClient | Client,
    body: TicketPatch,
) -> HTTPValidationError | TicketView | None:
    """Update Ticket

     Edit the fields. Only what the caller actually sent is touched — the
    unset fields are not passed to the store at all — so clearing a due date and
    leaving it alone are different requests rather than the same null.

    Args:
        key (str):
        body (TicketPatch): The editable fields. Every one is nullable AND defaulted, so "clear
            the
            due date" and "leave it alone" are told apart by which keys the caller
            actually sent (`model_fields_set`), not by the value.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | TicketView
    """

    return sync_detailed(
        key=key,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    key: str,
    *,
    client: AuthenticatedClient | Client,
    body: TicketPatch,
) -> Response[HTTPValidationError | TicketView]:
    """Update Ticket

     Edit the fields. Only what the caller actually sent is touched — the
    unset fields are not passed to the store at all — so clearing a due date and
    leaving it alone are different requests rather than the same null.

    Args:
        key (str):
        body (TicketPatch): The editable fields. Every one is nullable AND defaulted, so "clear
            the
            due date" and "leave it alone" are told apart by which keys the caller
            actually sent (`model_fields_set`), not by the value.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | TicketView]
    """

    kwargs = _get_kwargs(
        key=key,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    key: str,
    *,
    client: AuthenticatedClient | Client,
    body: TicketPatch,
) -> HTTPValidationError | TicketView | None:
    """Update Ticket

     Edit the fields. Only what the caller actually sent is touched — the
    unset fields are not passed to the store at all — so clearing a due date and
    leaving it alone are different requests rather than the same null.

    Args:
        key (str):
        body (TicketPatch): The editable fields. Every one is nullable AND defaulted, so "clear
            the
            due date" and "leave it alone" are told apart by which keys the caller
            actually sent (`model_fields_set`), not by the value.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | TicketView
    """

    return (
        await asyncio_detailed(
            key=key,
            client=client,
            body=body,
        )
    ).parsed
