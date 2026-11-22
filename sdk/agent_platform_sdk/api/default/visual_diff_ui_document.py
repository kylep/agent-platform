from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.ui_visual_diff_in import UiVisualDiffIn
from ...models.ui_visual_diff_out import UiVisualDiffOut
from ...types import Response


def _get_kwargs(
    slug: str,
    *,
    body: UiVisualDiffIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/ui-designer/docs/{slug}/visual-diff".format(
            slug=quote(str(slug), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | UiVisualDiffOut | None:
    if response.status_code == 200:
        response_200 = UiVisualDiffOut.from_dict(response.json())

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
) -> Response[HTTPValidationError | UiVisualDiffOut]:
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
    body: UiVisualDiffIn,
) -> Response[HTTPValidationError | UiVisualDiffOut]:
    """Visual Diff Ui Document

     Compare two explicit retained versions in the same world-coordinate canvas.

    Args:
        slug (str):
        body (UiVisualDiffIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiVisualDiffOut]
    """

    kwargs = _get_kwargs(
        slug=slug,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    body: UiVisualDiffIn,
) -> HTTPValidationError | UiVisualDiffOut | None:
    """Visual Diff Ui Document

     Compare two explicit retained versions in the same world-coordinate canvas.

    Args:
        slug (str):
        body (UiVisualDiffIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiVisualDiffOut
    """

    return sync_detailed(
        slug=slug,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    body: UiVisualDiffIn,
) -> Response[HTTPValidationError | UiVisualDiffOut]:
    """Visual Diff Ui Document

     Compare two explicit retained versions in the same world-coordinate canvas.

    Args:
        slug (str):
        body (UiVisualDiffIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiVisualDiffOut]
    """

    kwargs = _get_kwargs(
        slug=slug,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    body: UiVisualDiffIn,
) -> HTTPValidationError | UiVisualDiffOut | None:
    """Visual Diff Ui Document

     Compare two explicit retained versions in the same world-coordinate canvas.

    Args:
        slug (str):
        body (UiVisualDiffIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiVisualDiffOut
    """

    return (
        await asyncio_detailed(
            slug=slug,
            client=client,
            body=body,
        )
    ).parsed
