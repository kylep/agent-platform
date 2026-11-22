from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.ui_doc_get_out import UiDocGetOut
from ...types import UNSET, Response, Unset


def _get_kwargs(
    slug: str,
    *,
    node: None | str | Unset = UNSET,
    depth: int | Unset = 3,
    after: None | str | Unset = UNSET,
    mode: None | str | Unset = UNSET,
    resolve: bool | Unset = False,
    version: int | None | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_node: None | str | Unset
    if isinstance(node, Unset):
        json_node = UNSET
    else:
        json_node = node
    params["node"] = json_node

    params["depth"] = depth

    json_after: None | str | Unset
    if isinstance(after, Unset):
        json_after = UNSET
    else:
        json_after = after
    params["after"] = json_after

    json_mode: None | str | Unset
    if isinstance(mode, Unset):
        json_mode = UNSET
    else:
        json_mode = mode
    params["mode"] = json_mode

    params["resolve"] = resolve

    json_version: int | None | Unset
    if isinstance(version, Unset):
        json_version = UNSET
    else:
        json_version = version
    params["version"] = json_version

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/ui-designer/docs/{slug}".format(
            slug=quote(str(slug), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | UiDocGetOut | None:
    if response.status_code == 200:
        response_200 = UiDocGetOut.from_dict(response.json())

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
) -> Response[HTTPValidationError | UiDocGetOut]:
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
    node: None | str | Unset = UNSET,
    depth: int | Unset = 3,
    after: None | str | Unset = UNSET,
    mode: None | str | Unset = UNSET,
    resolve: bool | Unset = False,
    version: int | None | Unset = UNSET,
) -> Response[HTTPValidationError | UiDocGetOut]:
    """Get Ui Document

    Args:
        slug (str):
        node (None | str | Unset):
        depth (int | Unset):  Default: 3.
        after (None | str | Unset):
        mode (None | str | Unset):
        resolve (bool | Unset):  Default: False.
        version (int | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiDocGetOut]
    """

    kwargs = _get_kwargs(
        slug=slug,
        node=node,
        depth=depth,
        after=after,
        mode=mode,
        resolve=resolve,
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
    node: None | str | Unset = UNSET,
    depth: int | Unset = 3,
    after: None | str | Unset = UNSET,
    mode: None | str | Unset = UNSET,
    resolve: bool | Unset = False,
    version: int | None | Unset = UNSET,
) -> HTTPValidationError | UiDocGetOut | None:
    """Get Ui Document

    Args:
        slug (str):
        node (None | str | Unset):
        depth (int | Unset):  Default: 3.
        after (None | str | Unset):
        mode (None | str | Unset):
        resolve (bool | Unset):  Default: False.
        version (int | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiDocGetOut
    """

    return sync_detailed(
        slug=slug,
        client=client,
        node=node,
        depth=depth,
        after=after,
        mode=mode,
        resolve=resolve,
        version=version,
    ).parsed


async def asyncio_detailed(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    node: None | str | Unset = UNSET,
    depth: int | Unset = 3,
    after: None | str | Unset = UNSET,
    mode: None | str | Unset = UNSET,
    resolve: bool | Unset = False,
    version: int | None | Unset = UNSET,
) -> Response[HTTPValidationError | UiDocGetOut]:
    """Get Ui Document

    Args:
        slug (str):
        node (None | str | Unset):
        depth (int | Unset):  Default: 3.
        after (None | str | Unset):
        mode (None | str | Unset):
        resolve (bool | Unset):  Default: False.
        version (int | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiDocGetOut]
    """

    kwargs = _get_kwargs(
        slug=slug,
        node=node,
        depth=depth,
        after=after,
        mode=mode,
        resolve=resolve,
        version=version,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    node: None | str | Unset = UNSET,
    depth: int | Unset = 3,
    after: None | str | Unset = UNSET,
    mode: None | str | Unset = UNSET,
    resolve: bool | Unset = False,
    version: int | None | Unset = UNSET,
) -> HTTPValidationError | UiDocGetOut | None:
    """Get Ui Document

    Args:
        slug (str):
        node (None | str | Unset):
        depth (int | Unset):  Default: 3.
        after (None | str | Unset):
        mode (None | str | Unset):
        resolve (bool | Unset):  Default: False.
        version (int | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiDocGetOut
    """

    return (
        await asyncio_detailed(
            slug=slug,
            client=client,
            node=node,
            depth=depth,
            after=after,
            mode=mode,
            resolve=resolve,
            version=version,
        )
    ).parsed
