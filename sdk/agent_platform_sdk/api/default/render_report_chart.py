from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.chart_spec import ChartSpec
from ...models.chart_svg import ChartSvg
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    *,
    body: ChartSpec,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/report-kit/chart",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ChartSvg | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = ChartSvg.from_dict(response.json())

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
) -> Response[ChartSvg | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ChartSpec,
) -> Response[ChartSvg | HTTPValidationError]:
    """Render Report Chart

     Server-rendered, theme-aware SVG for embedding in report fragments —
    reports carry no JavaScript, so charts are static markup (reportcharts).
    Colors are design-token vars resolved by the report-kit shell.

    Args:
        body (ChartSpec):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ChartSvg | HTTPValidationError]
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
    body: ChartSpec,
) -> ChartSvg | HTTPValidationError | None:
    """Render Report Chart

     Server-rendered, theme-aware SVG for embedding in report fragments —
    reports carry no JavaScript, so charts are static markup (reportcharts).
    Colors are design-token vars resolved by the report-kit shell.

    Args:
        body (ChartSpec):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ChartSvg | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ChartSpec,
) -> Response[ChartSvg | HTTPValidationError]:
    """Render Report Chart

     Server-rendered, theme-aware SVG for embedding in report fragments —
    reports carry no JavaScript, so charts are static markup (reportcharts).
    Colors are design-token vars resolved by the report-kit shell.

    Args:
        body (ChartSpec):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ChartSvg | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: ChartSpec,
) -> ChartSvg | HTTPValidationError | None:
    """Render Report Chart

     Server-rendered, theme-aware SVG for embedding in report fragments —
    reports carry no JavaScript, so charts are static markup (reportcharts).
    Colors are design-token vars resolved by the report-kit shell.

    Args:
        body (ChartSpec):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ChartSvg | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
