from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    channel_id: str,
    *,
    after: None | str | Unset = UNSET,
    last_event_id: None | str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(last_event_id, Unset):
        headers["Last-Event-ID"] = last_event_id

    params: dict[str, Any] = {}

    json_after: None | str | Unset
    if isinstance(after, Unset):
        json_after = UNSET
    else:
        json_after = after
    params["after"] = json_after

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/relay/channels/{channel_id}/events".format(
            channel_id=quote(str(channel_id), safe=""),
        ),
        "params": params,
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = cast(Any, None)
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
) -> Response[Any | HTTPValidationError]:
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
    after: None | str | Unset = UNSET,
    last_event_id: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    """Relay Events

     The room, live: `message`, `reaction` and `presence` events as they
    happen, plus a heartbeat comment so nothing between here and the browser
    decides an idle stream is a dead one.

    Membership is resolved BEFORE the response starts: a 403 has to be a 403,
    not an event stream that opens and then says nothing.

    Args:
        channel_id (str):
        after (None | str | Unset):
        last_event_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        channel_id=channel_id,
        after=after,
        last_event_id=last_event_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    channel_id: str,
    *,
    client: AuthenticatedClient | Client,
    after: None | str | Unset = UNSET,
    last_event_id: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    """Relay Events

     The room, live: `message`, `reaction` and `presence` events as they
    happen, plus a heartbeat comment so nothing between here and the browser
    decides an idle stream is a dead one.

    Membership is resolved BEFORE the response starts: a 403 has to be a 403,
    not an event stream that opens and then says nothing.

    Args:
        channel_id (str):
        after (None | str | Unset):
        last_event_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        channel_id=channel_id,
        client=client,
        after=after,
        last_event_id=last_event_id,
    ).parsed


async def asyncio_detailed(
    channel_id: str,
    *,
    client: AuthenticatedClient | Client,
    after: None | str | Unset = UNSET,
    last_event_id: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    """Relay Events

     The room, live: `message`, `reaction` and `presence` events as they
    happen, plus a heartbeat comment so nothing between here and the browser
    decides an idle stream is a dead one.

    Membership is resolved BEFORE the response starts: a 403 has to be a 403,
    not an event stream that opens and then says nothing.

    Args:
        channel_id (str):
        after (None | str | Unset):
        last_event_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        channel_id=channel_id,
        after=after,
        last_event_id=last_event_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    channel_id: str,
    *,
    client: AuthenticatedClient | Client,
    after: None | str | Unset = UNSET,
    last_event_id: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    """Relay Events

     The room, live: `message`, `reaction` and `presence` events as they
    happen, plus a heartbeat comment so nothing between here and the browser
    decides an idle stream is a dead one.

    Membership is resolved BEFORE the response starts: a 403 has to be a 403,
    not an event stream that opens and then says nothing.

    Args:
        channel_id (str):
        after (None | str | Unset):
        last_event_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            channel_id=channel_id,
            client=client,
            after=after,
            last_event_id=last_event_id,
        )
    ).parsed
