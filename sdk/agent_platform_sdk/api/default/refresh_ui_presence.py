from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.ui_presence_in import UiPresenceIn
from ...models.ui_presence_out import UiPresenceOut
from ...types import Response


def _get_kwargs(
    slug: str,
    client_session: str,
    *,
    body: UiPresenceIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/api/ui-designer/docs/{slug}/presence/{client_session}".format(
            slug=quote(str(slug), safe=""),
            client_session=quote(str(client_session), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | UiPresenceOut | None:
    if response.status_code == 200:
        response_200 = UiPresenceOut.from_dict(response.json())

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
) -> Response[HTTPValidationError | UiPresenceOut]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    slug: str,
    client_session: str,
    *,
    client: AuthenticatedClient | Client,
    body: UiPresenceIn,
) -> Response[HTTPValidationError | UiPresenceOut]:
    """Refresh Ui Presence

    Args:
        slug (str):
        client_session (str):
        body (UiPresenceIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiPresenceOut]
    """

    kwargs = _get_kwargs(
        slug=slug,
        client_session=client_session,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    slug: str,
    client_session: str,
    *,
    client: AuthenticatedClient | Client,
    body: UiPresenceIn,
) -> HTTPValidationError | UiPresenceOut | None:
    """Refresh Ui Presence

    Args:
        slug (str):
        client_session (str):
        body (UiPresenceIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiPresenceOut
    """

    return sync_detailed(
        slug=slug,
        client_session=client_session,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    slug: str,
    client_session: str,
    *,
    client: AuthenticatedClient | Client,
    body: UiPresenceIn,
) -> Response[HTTPValidationError | UiPresenceOut]:
    """Refresh Ui Presence

    Args:
        slug (str):
        client_session (str):
        body (UiPresenceIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiPresenceOut]
    """

    kwargs = _get_kwargs(
        slug=slug,
        client_session=client_session,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    slug: str,
    client_session: str,
    *,
    client: AuthenticatedClient | Client,
    body: UiPresenceIn,
) -> HTTPValidationError | UiPresenceOut | None:
    """Refresh Ui Presence

    Args:
        slug (str):
        client_session (str):
        body (UiPresenceIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiPresenceOut
    """

    return (
        await asyncio_detailed(
            slug=slug,
            client_session=client_session,
            client=client,
            body=body,
        )
    ).parsed
