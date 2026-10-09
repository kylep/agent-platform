from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.room_watchers_in import RoomWatchersIn
from ...models.room_watchers_view import RoomWatchersView
from ...types import Response


def _get_kwargs(
    channel_id: str,
    *,
    body: RoomWatchersIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/api/relay/channels/{channel_id}/watchers".format(
            channel_id=quote(str(channel_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | RoomWatchersView | None:
    if response.status_code == 200:
        response_200 = RoomWatchersView.from_dict(response.json())

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
) -> Response[HTTPValidationError | RoomWatchersView]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    channel_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: RoomWatchersIn,
) -> Response[HTTPValidationError | RoomWatchersView]:
    """Put Room Watchers

    Args:
        channel_id (str):
        body (RoomWatchersIn): The room's complete, ordered watcher list (docs/design/41). Empty
            restores mention-only dispatch.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | RoomWatchersView]
    """

    kwargs = _get_kwargs(
        channel_id=channel_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    channel_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: RoomWatchersIn,
) -> HTTPValidationError | RoomWatchersView | None:
    """Put Room Watchers

    Args:
        channel_id (str):
        body (RoomWatchersIn): The room's complete, ordered watcher list (docs/design/41). Empty
            restores mention-only dispatch.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | RoomWatchersView
    """

    return sync_detailed(
        channel_id=channel_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    channel_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: RoomWatchersIn,
) -> Response[HTTPValidationError | RoomWatchersView]:
    """Put Room Watchers

    Args:
        channel_id (str):
        body (RoomWatchersIn): The room's complete, ordered watcher list (docs/design/41). Empty
            restores mention-only dispatch.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | RoomWatchersView]
    """

    kwargs = _get_kwargs(
        channel_id=channel_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    channel_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: RoomWatchersIn,
) -> HTTPValidationError | RoomWatchersView | None:
    """Put Room Watchers

    Args:
        channel_id (str):
        body (RoomWatchersIn): The room's complete, ordered watcher list (docs/design/41). Empty
            restores mention-only dispatch.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | RoomWatchersView
    """

    return (
        await asyncio_detailed(
            channel_id=channel_id,
            client=client,
            body=body,
        )
    ).parsed
