from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.ui_doc_delete_out import UiDocDeleteOut
from ...types import UNSET, Response, Unset


def _get_kwargs(
    slug: str,
    *,
    hard: bool | Unset = False,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["hard"] = hard

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/api/ui-designer/docs/{slug}".format(
            slug=quote(str(slug), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | UiDocDeleteOut | None:
    if response.status_code == 200:
        response_200 = UiDocDeleteOut.from_dict(response.json())

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
) -> Response[HTTPValidationError | UiDocDeleteOut]:
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
    hard: bool | Unset = False,
) -> Response[HTTPValidationError | UiDocDeleteOut]:
    """Delete Ui Document

     Soft delete: the owner, an `agents_edit` holder or the admin. `hard=true`
    removes the document, its versions, comments and placements now and runs
    asset GC: the administrator's browser session only.

    Args:
        slug (str):
        hard (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiDocDeleteOut]
    """

    kwargs = _get_kwargs(
        slug=slug,
        hard=hard,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    hard: bool | Unset = False,
) -> HTTPValidationError | UiDocDeleteOut | None:
    """Delete Ui Document

     Soft delete: the owner, an `agents_edit` holder or the admin. `hard=true`
    removes the document, its versions, comments and placements now and runs
    asset GC: the administrator's browser session only.

    Args:
        slug (str):
        hard (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiDocDeleteOut
    """

    return sync_detailed(
        slug=slug,
        client=client,
        hard=hard,
    ).parsed


async def asyncio_detailed(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    hard: bool | Unset = False,
) -> Response[HTTPValidationError | UiDocDeleteOut]:
    """Delete Ui Document

     Soft delete: the owner, an `agents_edit` holder or the admin. `hard=true`
    removes the document, its versions, comments and placements now and runs
    asset GC: the administrator's browser session only.

    Args:
        slug (str):
        hard (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiDocDeleteOut]
    """

    kwargs = _get_kwargs(
        slug=slug,
        hard=hard,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    hard: bool | Unset = False,
) -> HTTPValidationError | UiDocDeleteOut | None:
    """Delete Ui Document

     Soft delete: the owner, an `agents_edit` holder or the admin. `hard=true`
    removes the document, its versions, comments and placements now and runs
    asset GC: the administrator's browser session only.

    Args:
        slug (str):
        hard (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiDocDeleteOut
    """

    return (
        await asyncio_detailed(
            slug=slug,
            client=client,
            hard=hard,
        )
    ).parsed
