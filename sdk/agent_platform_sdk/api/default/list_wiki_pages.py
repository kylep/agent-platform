import datetime
from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.wiki_page_view import WikiPageView
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    q: None | str | Unset = UNSET,
    tag: None | str | Unset = UNSET,
    changed_since: datetime.datetime | None | Unset = UNSET,
    source_memory_id: None | str | Unset = UNSET,
    limit: int | Unset = 200,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_q: None | str | Unset
    if isinstance(q, Unset):
        json_q = UNSET
    else:
        json_q = q
    params["q"] = json_q

    json_tag: None | str | Unset
    if isinstance(tag, Unset):
        json_tag = UNSET
    else:
        json_tag = tag
    params["tag"] = json_tag

    json_changed_since: None | str | Unset
    if isinstance(changed_since, Unset):
        json_changed_since = UNSET
    elif isinstance(changed_since, datetime.datetime):
        json_changed_since = changed_since.isoformat()
    else:
        json_changed_since = changed_since
    params["changed_since"] = json_changed_since

    json_source_memory_id: None | str | Unset
    if isinstance(source_memory_id, Unset):
        json_source_memory_id = UNSET
    else:
        json_source_memory_id = source_memory_id
    params["source_memory_id"] = json_source_memory_id

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/wiki/pages",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[WikiPageView] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = WikiPageView.from_dict(response_200_item_data)

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
) -> Response[HTTPValidationError | list[WikiPageView]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    q: None | str | Unset = UNSET,
    tag: None | str | Unset = UNSET,
    changed_since: datetime.datetime | None | Unset = UNSET,
    source_memory_id: None | str | Unset = UNSET,
    limit: int | Unset = 200,
) -> Response[HTTPValidationError | list[WikiPageView]]:
    """List Wiki Pages

     The wiki, filtered. Newest first, or by relevance when `q` is a search
    the dialect can rank. Archived pages are excluded from all of it: they are
    out of the wiki by definition, and their history is read by slug.

    Args:
        q (None | str | Unset):
        tag (None | str | Unset):
        changed_since (datetime.datetime | None | Unset):
        source_memory_id (None | str | Unset):
        limit (int | Unset):  Default: 200.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[WikiPageView]]
    """

    kwargs = _get_kwargs(
        q=q,
        tag=tag,
        changed_since=changed_since,
        source_memory_id=source_memory_id,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    q: None | str | Unset = UNSET,
    tag: None | str | Unset = UNSET,
    changed_since: datetime.datetime | None | Unset = UNSET,
    source_memory_id: None | str | Unset = UNSET,
    limit: int | Unset = 200,
) -> HTTPValidationError | list[WikiPageView] | None:
    """List Wiki Pages

     The wiki, filtered. Newest first, or by relevance when `q` is a search
    the dialect can rank. Archived pages are excluded from all of it: they are
    out of the wiki by definition, and their history is read by slug.

    Args:
        q (None | str | Unset):
        tag (None | str | Unset):
        changed_since (datetime.datetime | None | Unset):
        source_memory_id (None | str | Unset):
        limit (int | Unset):  Default: 200.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[WikiPageView]
    """

    return sync_detailed(
        client=client,
        q=q,
        tag=tag,
        changed_since=changed_since,
        source_memory_id=source_memory_id,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    q: None | str | Unset = UNSET,
    tag: None | str | Unset = UNSET,
    changed_since: datetime.datetime | None | Unset = UNSET,
    source_memory_id: None | str | Unset = UNSET,
    limit: int | Unset = 200,
) -> Response[HTTPValidationError | list[WikiPageView]]:
    """List Wiki Pages

     The wiki, filtered. Newest first, or by relevance when `q` is a search
    the dialect can rank. Archived pages are excluded from all of it: they are
    out of the wiki by definition, and their history is read by slug.

    Args:
        q (None | str | Unset):
        tag (None | str | Unset):
        changed_since (datetime.datetime | None | Unset):
        source_memory_id (None | str | Unset):
        limit (int | Unset):  Default: 200.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[WikiPageView]]
    """

    kwargs = _get_kwargs(
        q=q,
        tag=tag,
        changed_since=changed_since,
        source_memory_id=source_memory_id,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    q: None | str | Unset = UNSET,
    tag: None | str | Unset = UNSET,
    changed_since: datetime.datetime | None | Unset = UNSET,
    source_memory_id: None | str | Unset = UNSET,
    limit: int | Unset = 200,
) -> HTTPValidationError | list[WikiPageView] | None:
    """List Wiki Pages

     The wiki, filtered. Newest first, or by relevance when `q` is a search
    the dialect can rank. Archived pages are excluded from all of it: they are
    out of the wiki by definition, and their history is read by slug.

    Args:
        q (None | str | Unset):
        tag (None | str | Unset):
        changed_since (datetime.datetime | None | Unset):
        source_memory_id (None | str | Unset):
        limit (int | Unset):  Default: 200.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[WikiPageView]
    """

    return (
        await asyncio_detailed(
            client=client,
            q=q,
            tag=tag,
            changed_since=changed_since,
            source_memory_id=source_memory_id,
            limit=limit,
        )
    ).parsed
