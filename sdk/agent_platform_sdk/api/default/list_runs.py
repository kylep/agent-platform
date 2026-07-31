from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.run_summary import RunSummary
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
    agent: None | str | Unset = UNSET,
    state: None | str | Unset = UNSET,
    tag: None | str | Unset = UNSET,
    needs_summary: bool | Unset = False,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    params["offset"] = offset

    json_agent: None | str | Unset
    if isinstance(agent, Unset):
        json_agent = UNSET
    else:
        json_agent = agent
    params["agent"] = json_agent

    json_state: None | str | Unset
    if isinstance(state, Unset):
        json_state = UNSET
    else:
        json_state = state
    params["state"] = json_state

    json_tag: None | str | Unset
    if isinstance(tag, Unset):
        json_tag = UNSET
    else:
        json_tag = tag
    params["tag"] = json_tag

    params["needs_summary"] = needs_summary

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/runs",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[RunSummary] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = RunSummary.from_dict(response_200_item_data)

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
) -> Response[HTTPValidationError | list[RunSummary]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
    agent: None | str | Unset = UNSET,
    state: None | str | Unset = UNSET,
    tag: None | str | Unset = UNSET,
    needs_summary: bool | Unset = False,
) -> Response[HTTPValidationError | list[RunSummary]]:
    """List Runs

     Run history with paging (`offset`) and agent/state filters pushed to
    SQL — the full history stays reachable, not just the newest window. The
    tag/needs_summary filters stay Python-side over a bounded recent window
    (JSON membership isn't portable across sqlite/postgres).

    Args:
        limit (int | Unset):  Default: 50.
        offset (int | Unset):  Default: 0.
        agent (None | str | Unset):
        state (None | str | Unset):
        tag (None | str | Unset):
        needs_summary (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[RunSummary]]
    """

    kwargs = _get_kwargs(
        limit=limit,
        offset=offset,
        agent=agent,
        state=state,
        tag=tag,
        needs_summary=needs_summary,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
    agent: None | str | Unset = UNSET,
    state: None | str | Unset = UNSET,
    tag: None | str | Unset = UNSET,
    needs_summary: bool | Unset = False,
) -> HTTPValidationError | list[RunSummary] | None:
    """List Runs

     Run history with paging (`offset`) and agent/state filters pushed to
    SQL — the full history stays reachable, not just the newest window. The
    tag/needs_summary filters stay Python-side over a bounded recent window
    (JSON membership isn't portable across sqlite/postgres).

    Args:
        limit (int | Unset):  Default: 50.
        offset (int | Unset):  Default: 0.
        agent (None | str | Unset):
        state (None | str | Unset):
        tag (None | str | Unset):
        needs_summary (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[RunSummary]
    """

    return sync_detailed(
        client=client,
        limit=limit,
        offset=offset,
        agent=agent,
        state=state,
        tag=tag,
        needs_summary=needs_summary,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
    agent: None | str | Unset = UNSET,
    state: None | str | Unset = UNSET,
    tag: None | str | Unset = UNSET,
    needs_summary: bool | Unset = False,
) -> Response[HTTPValidationError | list[RunSummary]]:
    """List Runs

     Run history with paging (`offset`) and agent/state filters pushed to
    SQL — the full history stays reachable, not just the newest window. The
    tag/needs_summary filters stay Python-side over a bounded recent window
    (JSON membership isn't portable across sqlite/postgres).

    Args:
        limit (int | Unset):  Default: 50.
        offset (int | Unset):  Default: 0.
        agent (None | str | Unset):
        state (None | str | Unset):
        tag (None | str | Unset):
        needs_summary (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[RunSummary]]
    """

    kwargs = _get_kwargs(
        limit=limit,
        offset=offset,
        agent=agent,
        state=state,
        tag=tag,
        needs_summary=needs_summary,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
    agent: None | str | Unset = UNSET,
    state: None | str | Unset = UNSET,
    tag: None | str | Unset = UNSET,
    needs_summary: bool | Unset = False,
) -> HTTPValidationError | list[RunSummary] | None:
    """List Runs

     Run history with paging (`offset`) and agent/state filters pushed to
    SQL — the full history stays reachable, not just the newest window. The
    tag/needs_summary filters stay Python-side over a bounded recent window
    (JSON membership isn't portable across sqlite/postgres).

    Args:
        limit (int | Unset):  Default: 50.
        offset (int | Unset):  Default: 0.
        agent (None | str | Unset):
        state (None | str | Unset):
        tag (None | str | Unset):
        needs_summary (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[RunSummary]
    """

    return (
        await asyncio_detailed(
            client=client,
            limit=limit,
            offset=offset,
            agent=agent,
            state=state,
            tag=tag,
            needs_summary=needs_summary,
        )
    ).parsed
