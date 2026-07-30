from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.ok_id import OkId
from ...types import Response


def _get_kwargs(
    conversation_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/api/conversations/{conversation_id}".format(
            conversation_id=quote(str(conversation_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | OkId | None:
    if response.status_code == 200:
        response_200 = OkId.from_dict(response.json())

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
) -> Response[HTTPValidationError | OkId]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    conversation_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | OkId]:
    """Delete Conversation

     Permanently delete a web conversation and its turns. Connector-owned
    conversations (Discord etc.) are not deletable here — their lifecycle
    belongs to the external channel, and a delete would just be recreated on
    the next inbound message.

    Args:
        conversation_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | OkId]
    """

    kwargs = _get_kwargs(
        conversation_id=conversation_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    conversation_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | OkId | None:
    """Delete Conversation

     Permanently delete a web conversation and its turns. Connector-owned
    conversations (Discord etc.) are not deletable here — their lifecycle
    belongs to the external channel, and a delete would just be recreated on
    the next inbound message.

    Args:
        conversation_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | OkId
    """

    return sync_detailed(
        conversation_id=conversation_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    conversation_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | OkId]:
    """Delete Conversation

     Permanently delete a web conversation and its turns. Connector-owned
    conversations (Discord etc.) are not deletable here — their lifecycle
    belongs to the external channel, and a delete would just be recreated on
    the next inbound message.

    Args:
        conversation_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | OkId]
    """

    kwargs = _get_kwargs(
        conversation_id=conversation_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    conversation_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | OkId | None:
    """Delete Conversation

     Permanently delete a web conversation and its turns. Connector-owned
    conversations (Discord etc.) are not deletable here — their lifecycle
    belongs to the external channel, and a delete would just be recreated on
    the next inbound message.

    Args:
        conversation_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | OkId
    """

    return (
        await asyncio_detailed(
            conversation_id=conversation_id,
            client=client,
        )
    ).parsed
