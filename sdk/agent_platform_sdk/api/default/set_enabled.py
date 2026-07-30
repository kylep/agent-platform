from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.schedule_toggle import ScheduleToggle
from ...types import Response


def _get_kwargs(
    agent: str,
    action: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/schedules/{agent}/{action}".format(
            agent=quote(str(agent), safe=""),
            action=quote(str(action), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | ScheduleToggle | None:
    if response.status_code == 200:
        response_200 = ScheduleToggle.from_dict(response.json())

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
) -> Response[HTTPValidationError | ScheduleToggle]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    agent: str,
    action: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | ScheduleToggle]:
    """Set Enabled

    Args:
        agent (str):
        action (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ScheduleToggle]
    """

    kwargs = _get_kwargs(
        agent=agent,
        action=action,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    agent: str,
    action: str,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | ScheduleToggle | None:
    """Set Enabled

    Args:
        agent (str):
        action (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ScheduleToggle
    """

    return sync_detailed(
        agent=agent,
        action=action,
        client=client,
    ).parsed


async def asyncio_detailed(
    agent: str,
    action: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | ScheduleToggle]:
    """Set Enabled

    Args:
        agent (str):
        action (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ScheduleToggle]
    """

    kwargs = _get_kwargs(
        agent=agent,
        action=action,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    agent: str,
    action: str,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | ScheduleToggle | None:
    """Set Enabled

    Args:
        agent (str):
        action (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ScheduleToggle
    """

    return (
        await asyncio_detailed(
            agent=agent,
            action=action,
            client=client,
        )
    ).parsed
