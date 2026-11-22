from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.ui_comment_out import UiCommentOut
from ...types import UNSET, Response, Unset


def _get_kwargs(
    slug: str,
    comment_id: str,
    *,
    reopen: bool | Unset = False,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["reopen"] = reopen

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/ui-designer/docs/{slug}/comments/{comment_id}/resolve".format(
            slug=quote(str(slug), safe=""),
            comment_id=quote(str(comment_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | UiCommentOut | None:
    if response.status_code == 200:
        response_200 = UiCommentOut.from_dict(response.json())

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
) -> Response[HTTPValidationError | UiCommentOut]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    slug: str,
    comment_id: str,
    *,
    client: AuthenticatedClient | Client,
    reopen: bool | Unset = False,
) -> Response[HTTPValidationError | UiCommentOut]:
    """Resolve Ui Comment

    Args:
        slug (str):
        comment_id (str):
        reopen (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiCommentOut]
    """

    kwargs = _get_kwargs(
        slug=slug,
        comment_id=comment_id,
        reopen=reopen,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    slug: str,
    comment_id: str,
    *,
    client: AuthenticatedClient | Client,
    reopen: bool | Unset = False,
) -> HTTPValidationError | UiCommentOut | None:
    """Resolve Ui Comment

    Args:
        slug (str):
        comment_id (str):
        reopen (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiCommentOut
    """

    return sync_detailed(
        slug=slug,
        comment_id=comment_id,
        client=client,
        reopen=reopen,
    ).parsed


async def asyncio_detailed(
    slug: str,
    comment_id: str,
    *,
    client: AuthenticatedClient | Client,
    reopen: bool | Unset = False,
) -> Response[HTTPValidationError | UiCommentOut]:
    """Resolve Ui Comment

    Args:
        slug (str):
        comment_id (str):
        reopen (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiCommentOut]
    """

    kwargs = _get_kwargs(
        slug=slug,
        comment_id=comment_id,
        reopen=reopen,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    slug: str,
    comment_id: str,
    *,
    client: AuthenticatedClient | Client,
    reopen: bool | Unset = False,
) -> HTTPValidationError | UiCommentOut | None:
    """Resolve Ui Comment

    Args:
        slug (str):
        comment_id (str):
        reopen (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiCommentOut
    """

    return (
        await asyncio_detailed(
            slug=slug,
            comment_id=comment_id,
            client=client,
            reopen=reopen,
        )
    ).parsed
