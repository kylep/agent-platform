from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.wiki_page_view import WikiPageView
from ...models.wiki_restore_in import WikiRestoreIn
from ...types import Response


def _get_kwargs(
    slug: str,
    *,
    body: WikiRestoreIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/wiki/pages/{slug}/restore".format(
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
    body: WikiRestoreIn,
) -> Response[HTTPValidationError | WikiPageView]:
    """Restore Wiki Page

     Bring a page back — from the archive, or back to one of its own versions,
    which is a new version rather than a rewritten history.

    Args:
        slug (str):
        body (WikiRestoreIn): Un-archive, or roll back. `version` absent is the un-archive; naming
            one
            is a roll-back, which is a new version rather than a rewritten history.

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
    body: WikiRestoreIn,
) -> HTTPValidationError | WikiPageView | None:
    """Restore Wiki Page

     Bring a page back — from the archive, or back to one of its own versions,
    which is a new version rather than a rewritten history.

    Args:
        slug (str):
        body (WikiRestoreIn): Un-archive, or roll back. `version` absent is the un-archive; naming
            one
            is a roll-back, which is a new version rather than a rewritten history.

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
    body: WikiRestoreIn,
) -> Response[HTTPValidationError | WikiPageView]:
    """Restore Wiki Page

     Bring a page back — from the archive, or back to one of its own versions,
    which is a new version rather than a rewritten history.

    Args:
        slug (str):
        body (WikiRestoreIn): Un-archive, or roll back. `version` absent is the un-archive; naming
            one
            is a roll-back, which is a new version rather than a rewritten history.

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
    body: WikiRestoreIn,
) -> HTTPValidationError | WikiPageView | None:
    """Restore Wiki Page

     Bring a page back — from the archive, or back to one of its own versions,
    which is a new version rather than a rewritten history.

    Args:
        slug (str):
        body (WikiRestoreIn): Un-archive, or roll back. `version` absent is the un-archive; naming
            one
            is a roll-back, which is a new version rather than a rewritten history.

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
