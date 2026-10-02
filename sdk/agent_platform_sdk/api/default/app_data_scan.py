from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.app_data_scan_response_app_data_scan import (
    AppDataScanResponseAppDataScan,
)
from ...models.http_validation_error import HTTPValidationError
from ...models.scan_in import ScanIn
from ...types import Response


def _get_kwargs(
    *,
    body: ScanIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/app-data/scan",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AppDataScanResponseAppDataScan | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = AppDataScanResponseAppDataScan.from_dict(response.json())

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
) -> Response[AppDataScanResponseAppDataScan | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ScanIn,
) -> Response[AppDataScanResponseAppDataScan | HTTPValidationError]:
    """App Data Scan

     Read a bounded page through one approved source role, as its viewer.

    Neither a run JWT nor an ordinary tool-call credential can enter. The
    source, current App fact, viewer facts and per-execution allowance all
    remain server-side, so the tool cannot enlarge its own read surface.

    Args:
        body (ScanIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AppDataScanResponseAppDataScan | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    body: ScanIn,
) -> AppDataScanResponseAppDataScan | HTTPValidationError | None:
    """App Data Scan

     Read a bounded page through one approved source role, as its viewer.

    Neither a run JWT nor an ordinary tool-call credential can enter. The
    source, current App fact, viewer facts and per-execution allowance all
    remain server-side, so the tool cannot enlarge its own read surface.

    Args:
        body (ScanIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AppDataScanResponseAppDataScan | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ScanIn,
) -> Response[AppDataScanResponseAppDataScan | HTTPValidationError]:
    """App Data Scan

     Read a bounded page through one approved source role, as its viewer.

    Neither a run JWT nor an ordinary tool-call credential can enter. The
    source, current App fact, viewer facts and per-execution allowance all
    remain server-side, so the tool cannot enlarge its own read surface.

    Args:
        body (ScanIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AppDataScanResponseAppDataScan | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: ScanIn,
) -> AppDataScanResponseAppDataScan | HTTPValidationError | None:
    """App Data Scan

     Read a bounded page through one approved source role, as its viewer.

    Neither a run JWT nor an ordinary tool-call credential can enter. The
    source, current App fact, viewer facts and per-execution allowance all
    remain server-side, so the tool cannot enlarge its own read surface.

    Args:
        body (ScanIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AppDataScanResponseAppDataScan | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
