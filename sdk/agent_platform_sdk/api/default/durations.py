from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.run_duration_point import RunDurationPoint
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    days: int | Unset = 14,
    agent: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["days"] = days

    json_agent: None | str | Unset
    if isinstance(agent, Unset):
        json_agent = UNSET
    else:
        json_agent = agent
    params["agent"] = json_agent

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/metrics/durations",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[RunDurationPoint] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = RunDurationPoint.from_dict(response_200_item_data)

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
) -> Response[HTTPValidationError | list[RunDurationPoint]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    days: int | Unset = 14,
    agent: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | list[RunDurationPoint]]:
    """Durations

     Individual run durations over time — the seconds-per-run chart's data.
    Raw points rather than pre-bucketed averages: at this platform's volume a
    scatter of real runs is more informative, and the client can average.
    Bounded by a day window (clamped 1–90) and a hard row cap.

    Args:
        days (int | Unset):  Default: 14.
        agent (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[RunDurationPoint]]
    """

    kwargs = _get_kwargs(
        days=days,
        agent=agent,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    days: int | Unset = 14,
    agent: None | str | Unset = UNSET,
) -> HTTPValidationError | list[RunDurationPoint] | None:
    """Durations

     Individual run durations over time — the seconds-per-run chart's data.
    Raw points rather than pre-bucketed averages: at this platform's volume a
    scatter of real runs is more informative, and the client can average.
    Bounded by a day window (clamped 1–90) and a hard row cap.

    Args:
        days (int | Unset):  Default: 14.
        agent (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[RunDurationPoint]
    """

    return sync_detailed(
        client=client,
        days=days,
        agent=agent,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    days: int | Unset = 14,
    agent: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | list[RunDurationPoint]]:
    """Durations

     Individual run durations over time — the seconds-per-run chart's data.
    Raw points rather than pre-bucketed averages: at this platform's volume a
    scatter of real runs is more informative, and the client can average.
    Bounded by a day window (clamped 1–90) and a hard row cap.

    Args:
        days (int | Unset):  Default: 14.
        agent (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[RunDurationPoint]]
    """

    kwargs = _get_kwargs(
        days=days,
        agent=agent,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    days: int | Unset = 14,
    agent: None | str | Unset = UNSET,
) -> HTTPValidationError | list[RunDurationPoint] | None:
    """Durations

     Individual run durations over time — the seconds-per-run chart's data.
    Raw points rather than pre-bucketed averages: at this platform's volume a
    scatter of real runs is more informative, and the client can average.
    Bounded by a day window (clamped 1–90) and a hard row cap.

    Args:
        days (int | Unset):  Default: 14.
        agent (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[RunDurationPoint]
    """

    return (
        await asyncio_detailed(
            client=client,
            days=days,
            agent=agent,
        )
    ).parsed
