from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.relay_message import RelayMessage
from ...models.relay_notify_in import RelayNotifyIn
from ...types import Response


def _get_kwargs(
    *,
    body: RelayNotifyIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/relay/notify",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | RelayMessage | None:
    if response.status_code == 201:
        response_201 = RelayMessage.from_dict(response.json())

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
) -> Response[HTTPValidationError | RelayMessage]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: RelayNotifyIn,
) -> Response[HTTPValidationError | RelayMessage]:
    """Relay Notify

     A system row into a room from a caller that is not a participant of it
    (docs/design/25): the tcms app's `app:tcms` key announcing a recorded run
    in `#qa`. Posting a MESSAGE is a member's act — an app is in no room and
    holds no voice there — so this is the platform's own card shape instead:
    `kind=event`, no mentions, so the text summons nobody and the router has
    nothing to route; no membership check, as none of the platform's cards
    have one; one line, so the room's most trusted voice cannot be made to say
    a second sentence by the text it relays. The author is the caller's
    principal, so the row still says who really wrote it.

    Args:
        body (RelayNotifyIn): A system row from something that is not a participant
            (docs/design/25):
            an app key announcing what it recorded. `channel` is a name, `#name` or an
            id; the text is flattened to one line and mentions in it summon nobody.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | RelayMessage]
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
    body: RelayNotifyIn,
) -> HTTPValidationError | RelayMessage | None:
    """Relay Notify

     A system row into a room from a caller that is not a participant of it
    (docs/design/25): the tcms app's `app:tcms` key announcing a recorded run
    in `#qa`. Posting a MESSAGE is a member's act — an app is in no room and
    holds no voice there — so this is the platform's own card shape instead:
    `kind=event`, no mentions, so the text summons nobody and the router has
    nothing to route; no membership check, as none of the platform's cards
    have one; one line, so the room's most trusted voice cannot be made to say
    a second sentence by the text it relays. The author is the caller's
    principal, so the row still says who really wrote it.

    Args:
        body (RelayNotifyIn): A system row from something that is not a participant
            (docs/design/25):
            an app key announcing what it recorded. `channel` is a name, `#name` or an
            id; the text is flattened to one line and mentions in it summon nobody.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | RelayMessage
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: RelayNotifyIn,
) -> Response[HTTPValidationError | RelayMessage]:
    """Relay Notify

     A system row into a room from a caller that is not a participant of it
    (docs/design/25): the tcms app's `app:tcms` key announcing a recorded run
    in `#qa`. Posting a MESSAGE is a member's act — an app is in no room and
    holds no voice there — so this is the platform's own card shape instead:
    `kind=event`, no mentions, so the text summons nobody and the router has
    nothing to route; no membership check, as none of the platform's cards
    have one; one line, so the room's most trusted voice cannot be made to say
    a second sentence by the text it relays. The author is the caller's
    principal, so the row still says who really wrote it.

    Args:
        body (RelayNotifyIn): A system row from something that is not a participant
            (docs/design/25):
            an app key announcing what it recorded. `channel` is a name, `#name` or an
            id; the text is flattened to one line and mentions in it summon nobody.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | RelayMessage]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: RelayNotifyIn,
) -> HTTPValidationError | RelayMessage | None:
    """Relay Notify

     A system row into a room from a caller that is not a participant of it
    (docs/design/25): the tcms app's `app:tcms` key announcing a recorded run
    in `#qa`. Posting a MESSAGE is a member's act — an app is in no room and
    holds no voice there — so this is the platform's own card shape instead:
    `kind=event`, no mentions, so the text summons nobody and the router has
    nothing to route; no membership check, as none of the platform's cards
    have one; one line, so the room's most trusted voice cannot be made to say
    a second sentence by the text it relays. The author is the caller's
    principal, so the row still says who really wrote it.

    Args:
        body (RelayNotifyIn): A system row from something that is not a participant
            (docs/design/25):
            an app key announcing what it recorded. `channel` is a name, `#name` or an
            id; the text is flattened to one line and mentions in it summon nobody.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | RelayMessage
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
