from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.list_ui_platform_components_detail_type_0 import (
    ListUiPlatformComponentsDetailType0,
)
from ...models.ui_platform_component_library_out import UiPlatformComponentLibraryOut
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    name: None | str | Unset = UNSET,
    detail: ListUiPlatformComponentsDetailType0 | None | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_name: None | str | Unset
    if isinstance(name, Unset):
        json_name = UNSET
    else:
        json_name = name
    params["name"] = json_name

    json_detail: None | str | Unset
    if isinstance(detail, Unset):
        json_detail = UNSET
    elif isinstance(detail, ListUiPlatformComponentsDetailType0):
        json_detail = detail.value
    else:
        json_detail = detail
    params["detail"] = json_detail

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/ui-designer/component-library",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | UiPlatformComponentLibraryOut | None:
    if response.status_code == 200:
        response_200 = UiPlatformComponentLibraryOut.from_dict(response.json())

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
) -> Response[HTTPValidationError | UiPlatformComponentLibraryOut]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    name: None | str | Unset = UNSET,
    detail: ListUiPlatformComponentsDetailType0 | None | Unset = UNSET,
) -> Response[HTTPValidationError | UiPlatformComponentLibraryOut]:
    """List Ui Platform Components

     List Agent Platform components. `detail=index` (the default for a personal API key
    without `name`) is a compact catalogue; `detail=full` (the default with `name`) adds variants'
    props, fixtures and limitations.

    Args:
        name (None | str | Unset):
        detail (ListUiPlatformComponentsDetailType0 | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiPlatformComponentLibraryOut]
    """

    kwargs = _get_kwargs(
        name=name,
        detail=detail,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    name: None | str | Unset = UNSET,
    detail: ListUiPlatformComponentsDetailType0 | None | Unset = UNSET,
) -> HTTPValidationError | UiPlatformComponentLibraryOut | None:
    """List Ui Platform Components

     List Agent Platform components. `detail=index` (the default for a personal API key
    without `name`) is a compact catalogue; `detail=full` (the default with `name`) adds variants'
    props, fixtures and limitations.

    Args:
        name (None | str | Unset):
        detail (ListUiPlatformComponentsDetailType0 | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiPlatformComponentLibraryOut
    """

    return sync_detailed(
        client=client,
        name=name,
        detail=detail,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    name: None | str | Unset = UNSET,
    detail: ListUiPlatformComponentsDetailType0 | None | Unset = UNSET,
) -> Response[HTTPValidationError | UiPlatformComponentLibraryOut]:
    """List Ui Platform Components

     List Agent Platform components. `detail=index` (the default for a personal API key
    without `name`) is a compact catalogue; `detail=full` (the default with `name`) adds variants'
    props, fixtures and limitations.

    Args:
        name (None | str | Unset):
        detail (ListUiPlatformComponentsDetailType0 | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiPlatformComponentLibraryOut]
    """

    kwargs = _get_kwargs(
        name=name,
        detail=detail,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    name: None | str | Unset = UNSET,
    detail: ListUiPlatformComponentsDetailType0 | None | Unset = UNSET,
) -> HTTPValidationError | UiPlatformComponentLibraryOut | None:
    """List Ui Platform Components

     List Agent Platform components. `detail=index` (the default for a personal API key
    without `name`) is a compact catalogue; `detail=full` (the default with `name`) adds variants'
    props, fixtures and limitations.

    Args:
        name (None | str | Unset):
        detail (ListUiPlatformComponentsDetailType0 | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiPlatformComponentLibraryOut
    """

    return (
        await asyncio_detailed(
            client=client,
            name=name,
            detail=detail,
        )
    ).parsed
