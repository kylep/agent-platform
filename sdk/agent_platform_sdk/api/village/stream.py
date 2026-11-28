from http import HTTPStatus
from typing import Any, cast

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    session_id: None | str | Unset = UNSET,
    after: int | Unset = 0,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_session_id: None | str | Unset
    if isinstance(session_id, Unset):
        json_session_id = UNSET
    else:
        json_session_id = session_id
    params["session_id"] = json_session_id

    params["after"] = after

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/village/stream",
        "params": params,
    }

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
    *,
    client: AuthenticatedClient | Client,
    session_id: None | str | Unset = UNSET,
    after: int | Unset = 0,
) -> Response[Any | HTTPValidationError]:
    """Stream

     SSE: `event` frames (public narrative rows past the cursor), `frame`
    frames (positions from the latest snapshot), `session` frames on a state
    change, a heartbeat in between.

    Args:
        session_id (None | str | Unset):
        after (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        session_id=session_id,
        after=after,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    session_id: None | str | Unset = UNSET,
    after: int | Unset = 0,
) -> Any | HTTPValidationError | None:
    """Stream

     SSE: `event` frames (public narrative rows past the cursor), `frame`
    frames (positions from the latest snapshot), `session` frames on a state
    change, a heartbeat in between.

    Args:
        session_id (None | str | Unset):
        after (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        session_id=session_id,
        after=after,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    session_id: None | str | Unset = UNSET,
    after: int | Unset = 0,
) -> Response[Any | HTTPValidationError]:
    """Stream

     SSE: `event` frames (public narrative rows past the cursor), `frame`
    frames (positions from the latest snapshot), `session` frames on a state
    change, a heartbeat in between.

    Args:
        session_id (None | str | Unset):
        after (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        session_id=session_id,
        after=after,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    session_id: None | str | Unset = UNSET,
    after: int | Unset = 0,
) -> Any | HTTPValidationError | None:
    """Stream

     SSE: `event` frames (public narrative rows past the cursor), `frame`
    frames (positions from the latest snapshot), `session` frames on a state
    change, a heartbeat in between.

    Args:
        session_id (None | str | Unset):
        after (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            session_id=session_id,
            after=after,
        )
    ).parsed
