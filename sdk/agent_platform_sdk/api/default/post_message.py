from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.message_accepted import MessageAccepted
from ...models.message_in import MessageIn
from ...types import Response


def _get_kwargs(
    conversation_id: str,
    *,
    body: MessageIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/conversations/{conversation_id}/messages".format(
            conversation_id=quote(str(conversation_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | MessageAccepted | None:
    if response.status_code == 200:
        response_200 = MessageAccepted.from_dict(response.json())

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
) -> Response[HTTPValidationError | MessageAccepted]:
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
    body: MessageIn,
) -> Response[HTTPValidationError | MessageAccepted]:
    """Post Message

     Continue the conversation: create the next turn (a run). Returns the run
    id; stream it via /api/runs/{id}/tail or poll the conversation.

    Args:
        conversation_id (str):
        body (MessageIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | MessageAccepted]
    """

    kwargs = _get_kwargs(
        conversation_id=conversation_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    conversation_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: MessageIn,
) -> HTTPValidationError | MessageAccepted | None:
    """Post Message

     Continue the conversation: create the next turn (a run). Returns the run
    id; stream it via /api/runs/{id}/tail or poll the conversation.

    Args:
        conversation_id (str):
        body (MessageIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | MessageAccepted
    """

    return sync_detailed(
        conversation_id=conversation_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    conversation_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: MessageIn,
) -> Response[HTTPValidationError | MessageAccepted]:
    """Post Message

     Continue the conversation: create the next turn (a run). Returns the run
    id; stream it via /api/runs/{id}/tail or poll the conversation.

    Args:
        conversation_id (str):
        body (MessageIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | MessageAccepted]
    """

    kwargs = _get_kwargs(
        conversation_id=conversation_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    conversation_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: MessageIn,
) -> HTTPValidationError | MessageAccepted | None:
    """Post Message

     Continue the conversation: create the next turn (a run). Returns the run
    id; stream it via /api/runs/{id}/tail or poll the conversation.

    Args:
        conversation_id (str):
        body (MessageIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | MessageAccepted
    """

    return (
        await asyncio_detailed(
            conversation_id=conversation_id,
            client=client,
            body=body,
        )
    ).parsed
