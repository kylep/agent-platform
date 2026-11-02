from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.ok_id import OkId
from ...types import UNSET, Response, Unset


def _get_kwargs(
    memory_id: str,
    *,
    expected_revision: None | str | Unset = UNSET,
    purge_history: bool | Unset = False,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_expected_revision: None | str | Unset
    if isinstance(expected_revision, Unset):
        json_expected_revision = UNSET
    else:
        json_expected_revision = expected_revision
    params["expected_revision"] = json_expected_revision

    params["purge_history"] = purge_history

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/api/memories/{memory_id}".format(
            memory_id=quote(str(memory_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | OkId | None:
    if response.status_code == 200:
        response_200 = OkId.from_dict(response.json())

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
) -> Response[HTTPValidationError | OkId]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    memory_id: str,
    *,
    client: AuthenticatedClient | Client,
    expected_revision: None | str | Unset = UNSET,
    purge_history: bool | Unset = False,
) -> Response[HTTPValidationError | OkId]:
    """Delete Memory

    Args:
        memory_id (str):
        expected_revision (None | str | Unset):
        purge_history (bool | Unset): admin only: also erase this memory's version history
            Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | OkId]
    """

    kwargs = _get_kwargs(
        memory_id=memory_id,
        expected_revision=expected_revision,
        purge_history=purge_history,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    memory_id: str,
    *,
    client: AuthenticatedClient | Client,
    expected_revision: None | str | Unset = UNSET,
    purge_history: bool | Unset = False,
) -> HTTPValidationError | OkId | None:
    """Delete Memory

    Args:
        memory_id (str):
        expected_revision (None | str | Unset):
        purge_history (bool | Unset): admin only: also erase this memory's version history
            Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | OkId
    """

    return sync_detailed(
        memory_id=memory_id,
        client=client,
        expected_revision=expected_revision,
        purge_history=purge_history,
    ).parsed


async def asyncio_detailed(
    memory_id: str,
    *,
    client: AuthenticatedClient | Client,
    expected_revision: None | str | Unset = UNSET,
    purge_history: bool | Unset = False,
) -> Response[HTTPValidationError | OkId]:
    """Delete Memory

    Args:
        memory_id (str):
        expected_revision (None | str | Unset):
        purge_history (bool | Unset): admin only: also erase this memory's version history
            Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | OkId]
    """

    kwargs = _get_kwargs(
        memory_id=memory_id,
        expected_revision=expected_revision,
        purge_history=purge_history,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    memory_id: str,
    *,
    client: AuthenticatedClient | Client,
    expected_revision: None | str | Unset = UNSET,
    purge_history: bool | Unset = False,
) -> HTTPValidationError | OkId | None:
    """Delete Memory

    Args:
        memory_id (str):
        expected_revision (None | str | Unset):
        purge_history (bool | Unset): admin only: also erase this memory's version history
            Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | OkId
    """

    return (
        await asyncio_detailed(
            memory_id=memory_id,
            client=client,
            expected_revision=expected_revision,
            purge_history=purge_history,
        )
    ).parsed
