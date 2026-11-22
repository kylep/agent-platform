from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    slug: str,
    *,
    mode: None | str | Unset = UNSET,
    version: int | None | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_mode: None | str | Unset
    if isinstance(mode, Unset):
        json_mode = UNSET
    else:
        json_mode = mode
    params["mode"] = json_mode

    json_version: int | None | Unset
    if isinstance(version, Unset):
        json_version = UNSET
    else:
        json_version = version
    params["version"] = json_version

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/ui-designer/docs/{slug}/resolved".format(
            slug=quote(str(slug), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = response.json()
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
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    mode: None | str | Unset = UNSET,
    version: int | None | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    """Ui Document Resolved

     The resolved tree (boxes, no glyph data) for the web overlay.

    Args:
        slug (str):
        mode (None | str | Unset):
        version (int | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        slug=slug,
        mode=mode,
        version=version,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    mode: None | str | Unset = UNSET,
    version: int | None | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    """Ui Document Resolved

     The resolved tree (boxes, no glyph data) for the web overlay.

    Args:
        slug (str):
        mode (None | str | Unset):
        version (int | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        slug=slug,
        client=client,
        mode=mode,
        version=version,
    ).parsed


async def asyncio_detailed(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    mode: None | str | Unset = UNSET,
    version: int | None | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    """Ui Document Resolved

     The resolved tree (boxes, no glyph data) for the web overlay.

    Args:
        slug (str):
        mode (None | str | Unset):
        version (int | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        slug=slug,
        mode=mode,
        version=version,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    mode: None | str | Unset = UNSET,
    version: int | None | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    """Ui Document Resolved

     The resolved tree (boxes, no glyph data) for the web overlay.

    Args:
        slug (str):
        mode (None | str | Unset):
        version (int | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            slug=slug,
            client=client,
            mode=mode,
            version=version,
        )
    ).parsed
