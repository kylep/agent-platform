from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.wiki_page_view import WikiPageView
from ...models.wiki_promote_in import WikiPromoteIn
from ...types import Response


def _get_kwargs(
    *,
    body: WikiPromoteIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/wiki/promote",
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
    *,
    client: AuthenticatedClient | Client,
    body: WikiPromoteIn,
) -> Response[HTTPValidationError | WikiPageView]:
    """Promote Memory

     Harden a memory into a page everybody can cite.

    A human promotes from any namespace and an agent only its own — a memory is
    a private note, and promoting somebody else's is publishing it for them. The
    row is read here rather than over HTTP (one process, one transaction), but
    through the memory API's own view so the shape the store is handed is the
    shape the memory endpoints serve.

    The slug and title come from the memory's key when the caller does not name
    them, which is what makes promoting the same memory twice land on the same
    page instead of a second copy.

    Args:
        body (WikiPromoteIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | WikiPageView]
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
    body: WikiPromoteIn,
) -> HTTPValidationError | WikiPageView | None:
    """Promote Memory

     Harden a memory into a page everybody can cite.

    A human promotes from any namespace and an agent only its own — a memory is
    a private note, and promoting somebody else's is publishing it for them. The
    row is read here rather than over HTTP (one process, one transaction), but
    through the memory API's own view so the shape the store is handed is the
    shape the memory endpoints serve.

    The slug and title come from the memory's key when the caller does not name
    them, which is what makes promoting the same memory twice land on the same
    page instead of a second copy.

    Args:
        body (WikiPromoteIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | WikiPageView
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: WikiPromoteIn,
) -> Response[HTTPValidationError | WikiPageView]:
    """Promote Memory

     Harden a memory into a page everybody can cite.

    A human promotes from any namespace and an agent only its own — a memory is
    a private note, and promoting somebody else's is publishing it for them. The
    row is read here rather than over HTTP (one process, one transaction), but
    through the memory API's own view so the shape the store is handed is the
    shape the memory endpoints serve.

    The slug and title come from the memory's key when the caller does not name
    them, which is what makes promoting the same memory twice land on the same
    page instead of a second copy.

    Args:
        body (WikiPromoteIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | WikiPageView]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: WikiPromoteIn,
) -> HTTPValidationError | WikiPageView | None:
    """Promote Memory

     Harden a memory into a page everybody can cite.

    A human promotes from any namespace and an agent only its own — a memory is
    a private note, and promoting somebody else's is publishing it for them. The
    row is read here rather than over HTTP (one process, one transaction), but
    through the memory API's own view so the shape the store is handed is the
    shape the memory endpoints serve.

    The slug and title come from the memory's key when the caller does not name
    them, which is what makes promoting the same memory twice land on the same
    page instead of a second copy.

    Args:
        body (WikiPromoteIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | WikiPageView
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
