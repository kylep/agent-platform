from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.quota import Quota
from ...models.refresh_quota_provider import RefreshQuotaProvider
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    provider: RefreshQuotaProvider | Unset = RefreshQuotaProvider.ALL,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_provider: str | Unset = UNSET
    if not isinstance(provider, Unset):
        json_provider = provider.value

    params["provider"] = json_provider

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/quota/refresh",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | Quota | None:
    if response.status_code == 200:
        response_200 = Quota.from_dict(response.json())

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
) -> Response[HTTPValidationError | Quota]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    provider: RefreshQuotaProvider | Unset = RefreshQuotaProvider.ALL,
) -> Response[HTTPValidationError | Quota]:
    """Refresh Quota

     Ask on purpose. Coalesced and rate-limited platform-wide, so this is
    safe to put behind a button and behind a tool.

    Everything happens under the lock, the short-circuit included: a caller
    that arrives during a probe waits for it and then finds the fresh snapshot
    it wrote, which is the same answer it would have got from its own probe and
    one fewer request to Anthropic.

    Args:
        provider (RefreshQuotaProvider | Unset):  Default: RefreshQuotaProvider.ALL.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | Quota]
    """

    kwargs = _get_kwargs(
        provider=provider,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    provider: RefreshQuotaProvider | Unset = RefreshQuotaProvider.ALL,
) -> HTTPValidationError | Quota | None:
    """Refresh Quota

     Ask on purpose. Coalesced and rate-limited platform-wide, so this is
    safe to put behind a button and behind a tool.

    Everything happens under the lock, the short-circuit included: a caller
    that arrives during a probe waits for it and then finds the fresh snapshot
    it wrote, which is the same answer it would have got from its own probe and
    one fewer request to Anthropic.

    Args:
        provider (RefreshQuotaProvider | Unset):  Default: RefreshQuotaProvider.ALL.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | Quota
    """

    return sync_detailed(
        client=client,
        provider=provider,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    provider: RefreshQuotaProvider | Unset = RefreshQuotaProvider.ALL,
) -> Response[HTTPValidationError | Quota]:
    """Refresh Quota

     Ask on purpose. Coalesced and rate-limited platform-wide, so this is
    safe to put behind a button and behind a tool.

    Everything happens under the lock, the short-circuit included: a caller
    that arrives during a probe waits for it and then finds the fresh snapshot
    it wrote, which is the same answer it would have got from its own probe and
    one fewer request to Anthropic.

    Args:
        provider (RefreshQuotaProvider | Unset):  Default: RefreshQuotaProvider.ALL.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | Quota]
    """

    kwargs = _get_kwargs(
        provider=provider,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    provider: RefreshQuotaProvider | Unset = RefreshQuotaProvider.ALL,
) -> HTTPValidationError | Quota | None:
    """Refresh Quota

     Ask on purpose. Coalesced and rate-limited platform-wide, so this is
    safe to put behind a button and behind a tool.

    Everything happens under the lock, the short-circuit included: a caller
    that arrives during a probe waits for it and then finds the fresh snapshot
    it wrote, which is the same answer it would have got from its own probe and
    one fewer request to Anthropic.

    Args:
        provider (RefreshQuotaProvider | Unset):  Default: RefreshQuotaProvider.ALL.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | Quota
    """

    return (
        await asyncio_detailed(
            client=client,
            provider=provider,
        )
    ).parsed
