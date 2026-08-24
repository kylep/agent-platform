from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.agent_def_out import AgentDefOut
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    name: str,
    version: int,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/agents/{name}/rollback/{version}".format(
            name=quote(str(name), safe=""),
            version=quote(str(version), safe=""),
        ),
    }

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
    version: int,
    *,
    client: AuthenticatedClient | Client,
) -> Response[AgentDefOut | HTTPValidationError]:
    """Rollback Agent

     Re-apply a logged snapshot as a NEW version. Rollback is a write like
    any other — the log is append-only, so undoing is recorded rather than
    erased, and it stays admin-only because it can restore any past grant set.

    The restored definition is re-validated: a snapshot naming a skill or tool
    the repo has since dropped is a dead grant now, and re-applying it would
    just quarantine the agent later.

    Args:
        name (str):
        version (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentDefOut | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        name=name,
        version=version,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    name: str,
    version: int,
    *,
    client: AuthenticatedClient | Client,
) -> AgentDefOut | HTTPValidationError | None:
    """Rollback Agent

     Re-apply a logged snapshot as a NEW version. Rollback is a write like
    any other — the log is append-only, so undoing is recorded rather than
    erased, and it stays admin-only because it can restore any past grant set.

    The restored definition is re-validated: a snapshot naming a skill or tool
    the repo has since dropped is a dead grant now, and re-applying it would
    just quarantine the agent later.

    Args:
        name (str):
        version (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentDefOut | HTTPValidationError
    """

    return sync_detailed(
        name=name,
        version=version,
        client=client,
    ).parsed


async def asyncio_detailed(
    name: str,
    version: int,
    *,
    client: AuthenticatedClient | Client,
) -> Response[AgentDefOut | HTTPValidationError]:
    """Rollback Agent

     Re-apply a logged snapshot as a NEW version. Rollback is a write like
    any other — the log is append-only, so undoing is recorded rather than
    erased, and it stays admin-only because it can restore any past grant set.

    The restored definition is re-validated: a snapshot naming a skill or tool
    the repo has since dropped is a dead grant now, and re-applying it would
    just quarantine the agent later.

    Args:
        name (str):
        version (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentDefOut | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        name=name,
        version=version,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    name: str,
    version: int,
    *,
    client: AuthenticatedClient | Client,
) -> AgentDefOut | HTTPValidationError | None:
    """Rollback Agent

     Re-apply a logged snapshot as a NEW version. Rollback is a write like
    any other — the log is append-only, so undoing is recorded rather than
    erased, and it stays admin-only because it can restore any past grant set.

    The restored definition is re-validated: a snapshot naming a skill or tool
    the repo has since dropped is a dead grant now, and re-applying it would
    just quarantine the agent later.

    Args:
        name (str):
        version (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentDefOut | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            name=name,
            version=version,
            client=client,
        )
    ).parsed
