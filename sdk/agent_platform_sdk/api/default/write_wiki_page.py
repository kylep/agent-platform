from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.wiki_page_view import WikiPageView
from ...models.wiki_write_in import WikiWriteIn
from ...types import Response


def _get_kwargs(
    slug: str,
    *,
    body: WikiWriteIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/api/wiki/pages/{slug}".format(
            slug=quote(str(slug), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | WikiPageView | None:
    if response.status_code == 200:
        response_200 = WikiPageView.from_dict(response.json())

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
) -> Response[HTTPValidationError | WikiPageView]:
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
    body: WikiWriteIn,
) -> Response[HTTPValidationError | WikiPageView]:
    """Write Wiki Page

     Replace a page's body, naming the version that was read. A mismatch is a
    409 carrying the current version and summary; the loser writes nothing.

    Args:
        slug (str):
        body (WikiWriteIn): A full replacement of a page's body. `base_version` is REQUIRED and
            unset is a 422: a write that never says what it read is a writer claiming
            the page has not moved without having looked, and the loser of two of those
            silently erases the winner.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | WikiPageView]
    """

    kwargs = _get_kwargs(
        slug=slug,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    body: WikiWriteIn,
) -> HTTPValidationError | WikiPageView | None:
    """Write Wiki Page

     Replace a page's body, naming the version that was read. A mismatch is a
    409 carrying the current version and summary; the loser writes nothing.

    Args:
        slug (str):
        body (WikiWriteIn): A full replacement of a page's body. `base_version` is REQUIRED and
            unset is a 422: a write that never says what it read is a writer claiming
            the page has not moved without having looked, and the loser of two of those
            silently erases the winner.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | WikiPageView
    """

    return sync_detailed(
        slug=slug,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    body: WikiWriteIn,
) -> Response[HTTPValidationError | WikiPageView]:
    """Write Wiki Page

     Replace a page's body, naming the version that was read. A mismatch is a
    409 carrying the current version and summary; the loser writes nothing.

    Args:
        slug (str):
        body (WikiWriteIn): A full replacement of a page's body. `base_version` is REQUIRED and
            unset is a 422: a write that never says what it read is a writer claiming
            the page has not moved without having looked, and the loser of two of those
            silently erases the winner.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | WikiPageView]
    """

    kwargs = _get_kwargs(
        slug=slug,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    body: WikiWriteIn,
) -> HTTPValidationError | WikiPageView | None:
    """Write Wiki Page

     Replace a page's body, naming the version that was read. A mismatch is a
    409 carrying the current version and summary; the loser writes nothing.

    Args:
        slug (str):
        body (WikiWriteIn): A full replacement of a page's body. `base_version` is REQUIRED and
            unset is a 422: a write that never says what it read is a writer claiming
            the page has not moved without having looked, and the loser of two of those
            silently erases the winner.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | WikiPageView
    """

    return (
        await asyncio_detailed(
            slug=slug,
            client=client,
            body=body,
        )
    ).parsed
