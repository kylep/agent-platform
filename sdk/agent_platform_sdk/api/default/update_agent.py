from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.agent_def_in import AgentDefIn
from ...models.agent_def_out import AgentDefOut
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    name: str,
    *,
    body: AgentDefIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/api/agents/{name}".format(
            name=quote(str(name), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AgentDefOut | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = AgentDefOut.from_dict(response.json())

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
) -> Response[AgentDefOut | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    name: str,
    *,
    client: AuthenticatedClient | Client,
    body: AgentDefIn,
) -> Response[AgentDefOut | HTTPValidationError]:
    """Update Agent

     Replace an agent's definition. The body is the WHOLE definition — an
    omitted field resets to its default — and any `name` in it is ignored: the
    path identifies the agent, so a payload can never rename or retarget one.

    A save that changes nothing is a no-op: it returns the row and appends no
    version, because the change log records changes, not visits.

    Args:
        name (str):
        body (AgentDefIn): A complete agent definition on the wire. PUT replaces the whole
            definition, so an omitted field RESETS to the default shown here rather
            than keeping whatever the row had.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentDefOut | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        name=name,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    name: str,
    *,
    client: AuthenticatedClient | Client,
    body: AgentDefIn,
) -> AgentDefOut | HTTPValidationError | None:
    """Update Agent

     Replace an agent's definition. The body is the WHOLE definition — an
    omitted field resets to its default — and any `name` in it is ignored: the
    path identifies the agent, so a payload can never rename or retarget one.

    A save that changes nothing is a no-op: it returns the row and appends no
    version, because the change log records changes, not visits.

    Args:
        name (str):
        body (AgentDefIn): A complete agent definition on the wire. PUT replaces the whole
            definition, so an omitted field RESETS to the default shown here rather
            than keeping whatever the row had.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentDefOut | HTTPValidationError
    """

    return sync_detailed(
        name=name,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    name: str,
    *,
    client: AuthenticatedClient | Client,
    body: AgentDefIn,
) -> Response[AgentDefOut | HTTPValidationError]:
    """Update Agent

     Replace an agent's definition. The body is the WHOLE definition — an
    omitted field resets to its default — and any `name` in it is ignored: the
    path identifies the agent, so a payload can never rename or retarget one.

    A save that changes nothing is a no-op: it returns the row and appends no
    version, because the change log records changes, not visits.

    Args:
        name (str):
        body (AgentDefIn): A complete agent definition on the wire. PUT replaces the whole
            definition, so an omitted field RESETS to the default shown here rather
            than keeping whatever the row had.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentDefOut | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        name=name,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    name: str,
    *,
    client: AuthenticatedClient | Client,
    body: AgentDefIn,
) -> AgentDefOut | HTTPValidationError | None:
    """Update Agent

     Replace an agent's definition. The body is the WHOLE definition — an
    omitted field resets to its default — and any `name` in it is ignored: the
    path identifies the agent, so a payload can never rename or retarget one.

    A save that changes nothing is a no-op: it returns the row and appends no
    version, because the change log records changes, not visits.

    Args:
        name (str):
        body (AgentDefIn): A complete agent definition on the wire. PUT replaces the whole
            definition, so an omitted field RESETS to the default shown here rather
            than keeping whatever the row had.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentDefOut | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            name=name,
            client=client,
            body=body,
        )
    ).parsed
