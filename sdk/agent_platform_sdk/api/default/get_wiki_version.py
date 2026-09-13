from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.wiki_diff_view import WikiDiffView
from ...types import Response


def _get_kwargs(
    slug: str,
    version: int,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/wiki/pages/{slug}/versions/{version}".format(
            slug=quote(str(slug), safe=""),
            version=quote(str(version), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | WikiDiffView | None:
    if response.status_code == 200:
        response_200 = WikiDiffView.from_dict(response.json())

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
) -> Response[HTTPValidationError | WikiDiffView]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    slug: str,
    version: int,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | WikiDiffView]:
    """Get Wiki Version

     One version's body and the diff that produced it. Computed on read from
    the two stored bodies, so nothing about a page's history depends on a patch
    having been written correctly at the time.

    Args:
        slug (str):
        version (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | WikiDiffView]
    """

    kwargs = _get_kwargs(
        slug=slug,
        version=version,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    slug: str,
    version: int,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | WikiDiffView | None:
    """Get Wiki Version

     One version's body and the diff that produced it. Computed on read from
    the two stored bodies, so nothing about a page's history depends on a patch
    having been written correctly at the time.

    Args:
        slug (str):
        version (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | WikiDiffView
    """

    return sync_detailed(
        slug=slug,
        version=version,
        client=client,
    ).parsed


async def asyncio_detailed(
    slug: str,
    version: int,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | WikiDiffView]:
    """Get Wiki Version

     One version's body and the diff that produced it. Computed on read from
    the two stored bodies, so nothing about a page's history depends on a patch
    having been written correctly at the time.

    Args:
        slug (str):
        version (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | WikiDiffView]
    """

    kwargs = _get_kwargs(
        slug=slug,
        version=version,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    slug: str,
    version: int,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | WikiDiffView | None:
    """Get Wiki Version

     One version's body and the diff that produced it. Computed on read from
    the two stored bodies, so nothing about a page's history depends on a patch
    having been written correctly at the time.

    Args:
        slug (str):
        version (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | WikiDiffView
    """

    return (
        await asyncio_detailed(
            slug=slug,
            version=version,
            client=client,
        )
    ).parsed
