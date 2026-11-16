from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.ui_doc_outline import UiDocOutline
from ...types import UNSET, Response, Unset


def _get_kwargs(
    slug: str,
    *,
    page: None | str | Unset = UNSET,
    node: None | str | Unset = UNSET,
    after: None | str | Unset = UNSET,
    mode: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_page: None | str | Unset
    if isinstance(page, Unset):
        json_page = UNSET
    else:
        json_page = page
    params["page"] = json_page

    json_node: None | str | Unset
    if isinstance(node, Unset):
        json_node = UNSET
    else:
        json_node = node
    params["node"] = json_node

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

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/ui-designer/docs/{slug}/outline".format(
            slug=quote(str(slug), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | UiDocOutline | None:
    if response.status_code == 200:
        response_200 = UiDocOutline.from_dict(response.json())

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
) -> Response[HTTPValidationError | UiDocOutline]:
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
    page: None | str | Unset = UNSET,
    node: None | str | Unset = UNSET,
    after: None | str | Unset = UNSET,
    mode: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | UiDocOutline]:
    """Ui Document Outline

    Args:
        slug (str):
        page (None | str | Unset):
        node (None | str | Unset):
        after (None | str | Unset):
        mode (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiDocOutline]
    """

    kwargs = _get_kwargs(
        slug=slug,
        page=page,
        node=node,
        after=after,
        mode=mode,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    page: None | str | Unset = UNSET,
    node: None | str | Unset = UNSET,
    after: None | str | Unset = UNSET,
    mode: None | str | Unset = UNSET,
) -> HTTPValidationError | UiDocOutline | None:
    """Ui Document Outline

    Args:
        slug (str):
        page (None | str | Unset):
        node (None | str | Unset):
        after (None | str | Unset):
        mode (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiDocOutline
    """

    return sync_detailed(
        slug=slug,
        client=client,
        page=page,
        node=node,
        after=after,
        mode=mode,
    ).parsed


async def asyncio_detailed(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    page: None | str | Unset = UNSET,
    node: None | str | Unset = UNSET,
    after: None | str | Unset = UNSET,
    mode: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | UiDocOutline]:
    """Ui Document Outline

    Args:
        slug (str):
        page (None | str | Unset):
        node (None | str | Unset):
        after (None | str | Unset):
        mode (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiDocOutline]
    """

    kwargs = _get_kwargs(
        slug=slug,
        page=page,
        node=node,
        after=after,
        mode=mode,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    page: None | str | Unset = UNSET,
    node: None | str | Unset = UNSET,
    after: None | str | Unset = UNSET,
    mode: None | str | Unset = UNSET,
) -> HTTPValidationError | UiDocOutline | None:
    """Ui Document Outline

    Args:
        slug (str):
        page (None | str | Unset):
        node (None | str | Unset):
        after (None | str | Unset):
        mode (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiDocOutline
    """

    return (
        await asyncio_detailed(
            slug=slug,
            client=client,
            page=page,
            node=node,
            after=after,
            mode=mode,
        )
    ).parsed
