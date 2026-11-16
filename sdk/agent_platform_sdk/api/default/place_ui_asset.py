from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.ui_doc_place_in import UiDocPlaceIn
from ...models.ui_doc_place_out import UiDocPlaceOut
from ...types import Response


def _get_kwargs(
    slug: str,
    *,
    body: UiDocPlaceIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/ui-designer/docs/{slug}/place".format(
            slug=quote(str(slug), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | UiDocPlaceOut | None:
    if response.status_code == 200:
        response_200 = UiDocPlaceOut.from_dict(response.json())

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
) -> Response[HTTPValidationError | UiDocPlaceOut]:
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
    body: UiDocPlaceIn,
) -> Response[HTTPValidationError | UiDocPlaceOut]:
    """Place Ui Asset

     Copy an image artifact into the document's assets and record the
    placement. The node then names the sha256; `insert`/`replace`/`set_doc`
    accept only placed assets (UD-ASSET-UNKNOWN).

    Args:
        slug (str):
        body (UiDocPlaceIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiDocPlaceOut]
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
    body: UiDocPlaceIn,
) -> HTTPValidationError | UiDocPlaceOut | None:
    """Place Ui Asset

     Copy an image artifact into the document's assets and record the
    placement. The node then names the sha256; `insert`/`replace`/`set_doc`
    accept only placed assets (UD-ASSET-UNKNOWN).

    Args:
        slug (str):
        body (UiDocPlaceIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiDocPlaceOut
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
    body: UiDocPlaceIn,
) -> Response[HTTPValidationError | UiDocPlaceOut]:
    """Place Ui Asset

     Copy an image artifact into the document's assets and record the
    placement. The node then names the sha256; `insert`/`replace`/`set_doc`
    accept only placed assets (UD-ASSET-UNKNOWN).

    Args:
        slug (str):
        body (UiDocPlaceIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiDocPlaceOut]
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
    body: UiDocPlaceIn,
) -> HTTPValidationError | UiDocPlaceOut | None:
    """Place Ui Asset

     Copy an image artifact into the document's assets and record the
    placement. The node then names the sha256; `insert`/`replace`/`set_doc`
    accept only placed assets (UD-ASSET-UNKNOWN).

    Args:
        slug (str):
        body (UiDocPlaceIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiDocPlaceOut
    """

    return (
        await asyncio_detailed(
            slug=slug,
            client=client,
            body=body,
        )
    ).parsed
