from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.relay_message import RelayMessage
from ...types import UNSET, Response, Unset


def _get_kwargs(
    channel_id: str,
    *,
    before: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    thread: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_before: None | str | Unset
    if isinstance(before, Unset):
        json_before = UNSET
    else:
        json_before = before
    params["before"] = json_before

    params["limit"] = limit

    json_thread: None | str | Unset
    if isinstance(thread, Unset):
        json_thread = UNSET
    else:
        json_thread = thread
    params["thread"] = json_thread

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/relay/channels/{channel_id}/messages".format(
            channel_id=quote(str(channel_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[RelayMessage] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = RelayMessage.from_dict(response_200_item_data)

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
) -> Response[HTTPValidationError | list[RelayMessage]]:
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
    before: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    thread: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | list[RelayMessage]]:
    """List Relay Messages

     A newest-first page. `before` is a message id rather than a timestamp so
    a client pages by what it already holds; `thread` narrows to one root and
    its replies.

    Args:
        channel_id (str):
        before (None | str | Unset):
        limit (int | Unset):  Default: 50.
        thread (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[RelayMessage]]
    """

    kwargs = _get_kwargs(
        channel_id=channel_id,
        before=before,
        limit=limit,
        thread=thread,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    channel_id: str,
    *,
    client: AuthenticatedClient | Client,
    before: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    thread: None | str | Unset = UNSET,
) -> HTTPValidationError | list[RelayMessage] | None:
    """List Relay Messages

     A newest-first page. `before` is a message id rather than a timestamp so
    a client pages by what it already holds; `thread` narrows to one root and
    its replies.

    Args:
        channel_id (str):
        before (None | str | Unset):
        limit (int | Unset):  Default: 50.
        thread (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[RelayMessage]
    """

    return sync_detailed(
        channel_id=channel_id,
        client=client,
        before=before,
        limit=limit,
        thread=thread,
    ).parsed


async def asyncio_detailed(
    channel_id: str,
    *,
    client: AuthenticatedClient | Client,
    before: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    thread: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | list[RelayMessage]]:
    """List Relay Messages

     A newest-first page. `before` is a message id rather than a timestamp so
    a client pages by what it already holds; `thread` narrows to one root and
    its replies.

    Args:
        channel_id (str):
        before (None | str | Unset):
        limit (int | Unset):  Default: 50.
        thread (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[RelayMessage]]
    """

    kwargs = _get_kwargs(
        channel_id=channel_id,
        before=before,
        limit=limit,
        thread=thread,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    channel_id: str,
    *,
    client: AuthenticatedClient | Client,
    before: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    thread: None | str | Unset = UNSET,
) -> HTTPValidationError | list[RelayMessage] | None:
    """List Relay Messages

     A newest-first page. `before` is a message id rather than a timestamp so
    a client pages by what it already holds; `thread` narrows to one root and
    its replies.

    Args:
        channel_id (str):
        before (None | str | Unset):
        limit (int | Unset):  Default: 50.
        thread (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[RelayMessage]
    """

    return (
        await asyncio_detailed(
            channel_id=channel_id,
            client=client,
            before=before,
            limit=limit,
            thread=thread,
        )
    ).parsed
