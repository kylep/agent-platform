from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.ui_library_apply_in import UiLibraryApplyIn
from ...models.ui_library_write_out import UiLibraryWriteOut
from ...types import Response


def _get_kwargs(
    slug: str,
    library_id: str,
    *,
    body: UiLibraryApplyIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/ui-designer/docs/{slug}/libraries/{library_id}/apply".format(
            slug=quote(str(slug), safe=""),
            library_id=quote(str(library_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | UiLibraryWriteOut | None:
    if response.status_code == 200:
        response_200 = UiLibraryWriteOut.from_dict(response.json())

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
) -> Response[HTTPValidationError | UiLibraryWriteOut]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    slug: str,
    library_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: UiLibraryApplyIn,
) -> Response[HTTPValidationError | UiLibraryWriteOut]:
    """Apply Ui Library

    Args:
        slug (str):
        library_id (str):
        body (UiLibraryApplyIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiLibraryWriteOut]
    """

    kwargs = _get_kwargs(
        slug=slug,
        library_id=library_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    slug: str,
    library_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: UiLibraryApplyIn,
) -> HTTPValidationError | UiLibraryWriteOut | None:
    """Apply Ui Library

    Args:
        slug (str):
        library_id (str):
        body (UiLibraryApplyIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiLibraryWriteOut
    """

    return sync_detailed(
        slug=slug,
        library_id=library_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    slug: str,
    library_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: UiLibraryApplyIn,
) -> Response[HTTPValidationError | UiLibraryWriteOut]:
    """Apply Ui Library

    Args:
        slug (str):
        library_id (str):
        body (UiLibraryApplyIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiLibraryWriteOut]
    """

    kwargs = _get_kwargs(
        slug=slug,
        library_id=library_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    slug: str,
    library_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: UiLibraryApplyIn,
) -> HTTPValidationError | UiLibraryWriteOut | None:
    """Apply Ui Library

    Args:
        slug (str):
        library_id (str):
        body (UiLibraryApplyIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiLibraryWriteOut
    """

    return (
        await asyncio_detailed(
            slug=slug,
            library_id=library_id,
            client=client,
            body=body,
        )
    ).parsed
