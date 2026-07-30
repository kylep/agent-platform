from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.memory_view import MemoryView
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    agent: None | str | Unset = UNSET,
    q: None | str | Unset = UNSET,
    limit: int | Unset = 50,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_agent: None | str | Unset
    if isinstance(agent, Unset):
        json_agent = UNSET
    else:
        json_agent = agent
    params["agent"] = json_agent

    json_q: None | str | Unset
    if isinstance(q, Unset):
        json_q = UNSET
    else:
        json_q = q
    params["q"] = json_q

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/memories",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[MemoryView] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = MemoryView.from_dict(response_200_item_data)

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
) -> Response[HTTPValidationError | list[MemoryView]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    agent: None | str | Unset = UNSET,
    q: None | str | Unset = UNSET,
    limit: int | Unset = 50,
) -> Response[HTTPValidationError | list[MemoryView]]:
    """List Memories

     List or search memories in a namespace. `q` is split into terms; a memory
    matches when every term appears (case-insensitive) in its content or key.
    Portable across sqlite/postgres (no engine-specific FTS).

    Args:
        agent (None | str | Unset):
        q (None | str | Unset):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[MemoryView]]
    """

    kwargs = _get_kwargs(
        agent=agent,
        q=q,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    agent: None | str | Unset = UNSET,
    q: None | str | Unset = UNSET,
    limit: int | Unset = 50,
) -> HTTPValidationError | list[MemoryView] | None:
    """List Memories

     List or search memories in a namespace. `q` is split into terms; a memory
    matches when every term appears (case-insensitive) in its content or key.
    Portable across sqlite/postgres (no engine-specific FTS).

    Args:
        agent (None | str | Unset):
        q (None | str | Unset):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[MemoryView]
    """

    return sync_detailed(
        client=client,
        agent=agent,
        q=q,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    agent: None | str | Unset = UNSET,
    q: None | str | Unset = UNSET,
    limit: int | Unset = 50,
) -> Response[HTTPValidationError | list[MemoryView]]:
    """List Memories

     List or search memories in a namespace. `q` is split into terms; a memory
    matches when every term appears (case-insensitive) in its content or key.
    Portable across sqlite/postgres (no engine-specific FTS).

    Args:
        agent (None | str | Unset):
        q (None | str | Unset):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[MemoryView]]
    """

    kwargs = _get_kwargs(
        agent=agent,
        q=q,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    agent: None | str | Unset = UNSET,
    q: None | str | Unset = UNSET,
    limit: int | Unset = 50,
) -> HTTPValidationError | list[MemoryView] | None:
    """List Memories

     List or search memories in a namespace. `q` is split into terms; a memory
    matches when every term appears (case-insensitive) in its content or key.
    Portable across sqlite/postgres (no engine-specific FTS).

    Args:
        agent (None | str | Unset):
        q (None | str | Unset):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[MemoryView]
    """

    return (
        await asyncio_detailed(
            client=client,
            agent=agent,
            q=q,
            limit=limit,
        )
    ).parsed
