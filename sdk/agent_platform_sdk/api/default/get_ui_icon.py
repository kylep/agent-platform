from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.ui_icon_out import UiIconOut
from ...types import UNSET, Response


def _get_kwargs(
    icon_id: str,
    *,
    revision: int,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["revision"] = revision

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/ui-designer/icons/{icon_id}".format(
            icon_id=quote(str(icon_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | UiIconOut | None:
    if response.status_code == 200:
        response_200 = UiIconOut.from_dict(response.json())

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
) -> Response[HTTPValidationError | UiIconOut]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    icon_id: str,
    *,
    client: AuthenticatedClient | Client,
    revision: int,
) -> Response[HTTPValidationError | UiIconOut]:
    """Get Ui Icon

    Args:
        icon_id (str):
        revision (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiIconOut]
    """

    kwargs = _get_kwargs(
        icon_id=icon_id,
        revision=revision,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    icon_id: str,
    *,
    client: AuthenticatedClient | Client,
    revision: int,
) -> HTTPValidationError | UiIconOut | None:
    """Get Ui Icon

    Args:
        icon_id (str):
        revision (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiIconOut
    """

    return sync_detailed(
        icon_id=icon_id,
        client=client,
        revision=revision,
    ).parsed


async def asyncio_detailed(
    icon_id: str,
    *,
    client: AuthenticatedClient | Client,
    revision: int,
) -> Response[HTTPValidationError | UiIconOut]:
    """Get Ui Icon

    Args:
        icon_id (str):
        revision (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiIconOut]
    """

    kwargs = _get_kwargs(
        icon_id=icon_id,
        revision=revision,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    icon_id: str,
    *,
    client: AuthenticatedClient | Client,
    revision: int,
) -> HTTPValidationError | UiIconOut | None:
    """Get Ui Icon

    Args:
        icon_id (str):
        revision (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiIconOut
    """

    return (
        await asyncio_detailed(
            icon_id=icon_id,
            client=client,
            revision=revision,
        )
    ).parsed
