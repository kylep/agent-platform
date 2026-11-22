from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    slug: str,
    client_session: str,
    *,
    document_id: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_document_id: None | str | Unset
    if isinstance(document_id, Unset):
        json_document_id = UNSET
    else:
        json_document_id = document_id
    params["document_id"] = json_document_id

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/api/ui-designer/docs/{slug}/presence/{client_session}".format(
            slug=quote(str(slug), safe=""),
            client_session=quote(str(client_session), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | HTTPValidationError | None:
    if response.status_code == 204:
        response_204 = cast(Any, None)
        return response_204

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
    client_session: str,
    *,
    client: AuthenticatedClient | Client,
    document_id: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    """Leave Ui Presence

    Args:
        slug (str):
        client_session (str):
        document_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        slug=slug,
        client_session=client_session,
        document_id=document_id,
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
    document_id: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    """Leave Ui Presence

    Args:
        slug (str):
        client_session (str):
        document_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        slug=slug,
        client_session=client_session,
        client=client,
        document_id=document_id,
    ).parsed


async def asyncio_detailed(
    slug: str,
    client_session: str,
    *,
    client: AuthenticatedClient | Client,
    document_id: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    """Leave Ui Presence

    Args:
        slug (str):
        client_session (str):
        document_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        slug=slug,
        client_session=client_session,
        document_id=document_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    slug: str,
    client_session: str,
    *,
    client: AuthenticatedClient | Client,
    document_id: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    """Leave Ui Presence

    Args:
        slug (str):
        client_session (str):
        document_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            slug=slug,
            client_session=client_session,
            client=client,
            document_id=document_id,
        )
    ).parsed
