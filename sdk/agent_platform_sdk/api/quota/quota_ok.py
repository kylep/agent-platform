from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.quota_ok import QuotaOk
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/quota/ok",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> QuotaOk | None:
    if response.status_code == 200:
        response_200 = QuotaOk.from_dict(response.json())

        return response_200

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[QuotaOk]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[QuotaOk]:
    """Quota Ok

     May the caller start expensive work now? One boolean, and what it was
    made from (docs/design/24).

    The reading is what the platform already holds unless that is stale, in
    which case this is one `refresh` — the same lock and the same
    short-circuit, so two engineers starting at once cost one probe and a
    loop costs one per window. A refresh that cannot run is not a 503 here:
    the row is still there, `ok` is computed from it, and `stale: true` says
    how much that is worth. The thresholds are the caller's own row's when
    the token names an agent, and the column defaults for a person — a
    human asking is asking what an ordinary agent would be told.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[QuotaOk]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> QuotaOk | None:
    """Quota Ok

     May the caller start expensive work now? One boolean, and what it was
    made from (docs/design/24).

    The reading is what the platform already holds unless that is stale, in
    which case this is one `refresh` — the same lock and the same
    short-circuit, so two engineers starting at once cost one probe and a
    loop costs one per window. A refresh that cannot run is not a 503 here:
    the row is still there, `ok` is computed from it, and `stale: true` says
    how much that is worth. The thresholds are the caller's own row's when
    the token names an agent, and the column defaults for a person — a
    human asking is asking what an ordinary agent would be told.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        QuotaOk
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[QuotaOk]:
    """Quota Ok

     May the caller start expensive work now? One boolean, and what it was
    made from (docs/design/24).

    The reading is what the platform already holds unless that is stale, in
    which case this is one `refresh` — the same lock and the same
    short-circuit, so two engineers starting at once cost one probe and a
    loop costs one per window. A refresh that cannot run is not a 503 here:
    the row is still there, `ok` is computed from it, and `stale: true` says
    how much that is worth. The thresholds are the caller's own row's when
    the token names an agent, and the column defaults for a person — a
    human asking is asking what an ordinary agent would be told.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[QuotaOk]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> QuotaOk | None:
    """Quota Ok

     May the caller start expensive work now? One boolean, and what it was
    made from (docs/design/24).

    The reading is what the platform already holds unless that is stale, in
    which case this is one `refresh` — the same lock and the same
    short-circuit, so two engineers starting at once cost one probe and a
    loop costs one per window. A refresh that cannot run is not a 503 here:
    the row is still there, `ok` is computed from it, and `stale: true` says
    how much that is worth. The thresholds are the caller's own row's when
    the token names an agent, and the column defaults for a person — a
    human asking is asking what an ordinary agent would be told.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        QuotaOk
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
