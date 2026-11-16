from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.ui_comment_list import UiCommentList
from ...types import UNSET, Response, Unset


def _get_kwargs(
    slug: str,
    *,
    node: None | str | Unset = UNSET,
    resolved: bool | Unset = True,
    limit: int | Unset = 200,
    before: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_node: None | str | Unset
    if isinstance(node, Unset):
        json_node = UNSET
    else:
        json_node = node
    params["node"] = json_node

    params["resolved"] = resolved

    params["limit"] = limit

    json_before: None | str | Unset
    if isinstance(before, Unset):
        json_before = UNSET
    else:
        json_before = before
    params["before"] = json_before

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/ui-designer/docs/{slug}/comments".format(
            slug=quote(str(slug), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | UiCommentList | None:
    if response.status_code == 200:
        response_200 = UiCommentList.from_dict(response.json())

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
) -> Response[HTTPValidationError | UiCommentList]:
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
    resolved: bool | Unset = True,
    limit: int | Unset = 200,
    before: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | UiCommentList]:
    """List Ui Comments

    Args:
        slug (str):
        node (None | str | Unset):
        resolved (bool | Unset):  Default: True.
        limit (int | Unset):  Default: 200.
        before (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiCommentList]
    """

    kwargs = _get_kwargs(
        slug=slug,
        node=node,
        resolved=resolved,
        limit=limit,
        before=before,
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
    resolved: bool | Unset = True,
    limit: int | Unset = 200,
    before: None | str | Unset = UNSET,
) -> HTTPValidationError | UiCommentList | None:
    """List Ui Comments

    Args:
        slug (str):
        node (None | str | Unset):
        resolved (bool | Unset):  Default: True.
        limit (int | Unset):  Default: 200.
        before (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiCommentList
    """

    return sync_detailed(
        slug=slug,
        client=client,
        node=node,
        resolved=resolved,
        limit=limit,
        before=before,
    ).parsed


async def asyncio_detailed(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    node: None | str | Unset = UNSET,
    resolved: bool | Unset = True,
    limit: int | Unset = 200,
    before: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | UiCommentList]:
    """List Ui Comments

    Args:
        slug (str):
        node (None | str | Unset):
        resolved (bool | Unset):  Default: True.
        limit (int | Unset):  Default: 200.
        before (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiCommentList]
    """

    kwargs = _get_kwargs(
        slug=slug,
        node=node,
        resolved=resolved,
        limit=limit,
        before=before,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    node: None | str | Unset = UNSET,
    resolved: bool | Unset = True,
    limit: int | Unset = 200,
    before: None | str | Unset = UNSET,
) -> HTTPValidationError | UiCommentList | None:
    """List Ui Comments

    Args:
        slug (str):
        node (None | str | Unset):
        resolved (bool | Unset):  Default: True.
        limit (int | Unset):  Default: 200.
        before (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiCommentList
    """

    return (
        await asyncio_detailed(
            slug=slug,
            client=client,
            node=node,
            resolved=resolved,
            limit=limit,
            before=before,
        )
    ).parsed
