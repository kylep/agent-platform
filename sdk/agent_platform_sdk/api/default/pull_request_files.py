from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.pull_request_file import PullRequestFile
from ...types import Response


def _get_kwargs(
    number: int,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/pull-requests/{number}/files".format(
            number=quote(str(number), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[PullRequestFile] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = PullRequestFile.from_dict(response_200_item_data)

            response_200.append(response_200_item)

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
) -> Response[HTTPValidationError | list[PullRequestFile]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    number: int,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | list[PullRequestFile]]:
    """Pull Request Files

     Changed files + unified diff for the Pending Changes detail view.

    Args:
        number (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[PullRequestFile]]
    """

    kwargs = _get_kwargs(
        number=number,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    number: int,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | list[PullRequestFile] | None:
    """Pull Request Files

     Changed files + unified diff for the Pending Changes detail view.

    Args:
        number (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[PullRequestFile]
    """

    return sync_detailed(
        number=number,
        client=client,
    ).parsed


async def asyncio_detailed(
    number: int,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | list[PullRequestFile]]:
    """Pull Request Files

     Changed files + unified diff for the Pending Changes detail view.

    Args:
        number (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[PullRequestFile]]
    """

    kwargs = _get_kwargs(
        number=number,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    number: int,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | list[PullRequestFile] | None:
    """Pull Request Files

     Changed files + unified diff for the Pending Changes detail view.

    Args:
        number (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[PullRequestFile]
    """

    return (
        await asyncio_detailed(
            number=number,
            client=client,
        )
    ).parsed
