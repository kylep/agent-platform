from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.wiki_history_row import WikiHistoryRow
from ...types import UNSET, Response, Unset


def _get_kwargs(
    slug: str,
    *,
    limit: int | Unset = 50,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/wiki/pages/{slug}/history".format(
            slug=quote(str(slug), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[WikiHistoryRow] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = WikiHistoryRow.from_dict(response_200_item_data)

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
) -> Response[HTTPValidationError | list[WikiHistoryRow]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
) -> Response[HTTPValidationError | list[WikiHistoryRow]]:
    """Wiki Page History

     A page's versions, newest first, each with what it changed. Readable for
    an archived page: the history is the record archiving keeps.

    Args:
        slug (str):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[WikiHistoryRow]]
    """

    kwargs = _get_kwargs(
        slug=slug,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
) -> HTTPValidationError | list[WikiHistoryRow] | None:
    """Wiki Page History

     A page's versions, newest first, each with what it changed. Readable for
    an archived page: the history is the record archiving keeps.

    Args:
        slug (str):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[WikiHistoryRow]
    """

    return sync_detailed(
        slug=slug,
        client=client,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
) -> Response[HTTPValidationError | list[WikiHistoryRow]]:
    """Wiki Page History

     A page's versions, newest first, each with what it changed. Readable for
    an archived page: the history is the record archiving keeps.

    Args:
        slug (str):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[WikiHistoryRow]]
    """

    kwargs = _get_kwargs(
        slug=slug,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
) -> HTTPValidationError | list[WikiHistoryRow] | None:
    """Wiki Page History

     A page's versions, newest first, each with what it changed. Readable for
    an archived page: the history is the record archiving keeps.

    Args:
        slug (str):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[WikiHistoryRow]
    """

    return (
        await asyncio_detailed(
            slug=slug,
            client=client,
            limit=limit,
        )
    ).parsed
