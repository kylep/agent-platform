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
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/api/agents/{name}".format(
            name=quote(str(name), safe=""),
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
    *,
    client: AuthenticatedClient | Client,
) -> Response[AgentDefOut | HTTPValidationError]:
    """Delete Agent

     Delete an agent's definition. Its runs, memories and change log survive.

    Deleting is a write, so it logs one: a TOMBSTONE version whose snapshot is
    the definition as it stood at the moment of deletion, and whose
    `changed_via` is the normal label prefixed `delete:` (`delete:admin`,
    `delete:tool:agents_edit`). The prefix is the whole marker — snapshots stay
    uniformly parseable as definitions, with no synthetic keys inside them — and
    it means the log alone is enough to say who removed an agent and to
    recreate it. System agents are platform-internal and refuse deletion.

    Its webhook secrets do NOT survive: they are credentials for paths that no
    longer exist, and the change log's tombstone snapshot deliberately doesn't
    carry them, so leaving the rows behind would only mean a recreated agent
    silently inheriting a secret nobody can see (docs/design/16).

    Args:
        name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentDefOut | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        name=name,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    name: str,
    *,
    client: AuthenticatedClient | Client,
) -> AgentDefOut | HTTPValidationError | None:
    """Delete Agent

     Delete an agent's definition. Its runs, memories and change log survive.

    Deleting is a write, so it logs one: a TOMBSTONE version whose snapshot is
    the definition as it stood at the moment of deletion, and whose
    `changed_via` is the normal label prefixed `delete:` (`delete:admin`,
    `delete:tool:agents_edit`). The prefix is the whole marker — snapshots stay
    uniformly parseable as definitions, with no synthetic keys inside them — and
    it means the log alone is enough to say who removed an agent and to
    recreate it. System agents are platform-internal and refuse deletion.

    Its webhook secrets do NOT survive: they are credentials for paths that no
    longer exist, and the change log's tombstone snapshot deliberately doesn't
    carry them, so leaving the rows behind would only mean a recreated agent
    silently inheriting a secret nobody can see (docs/design/16).

    Args:
        name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentDefOut | HTTPValidationError
    """

    return sync_detailed(
        name=name,
        client=client,
    ).parsed


async def asyncio_detailed(
    name: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[AgentDefOut | HTTPValidationError]:
    """Delete Agent

     Delete an agent's definition. Its runs, memories and change log survive.

    Deleting is a write, so it logs one: a TOMBSTONE version whose snapshot is
    the definition as it stood at the moment of deletion, and whose
    `changed_via` is the normal label prefixed `delete:` (`delete:admin`,
    `delete:tool:agents_edit`). The prefix is the whole marker — snapshots stay
    uniformly parseable as definitions, with no synthetic keys inside them — and
    it means the log alone is enough to say who removed an agent and to
    recreate it. System agents are platform-internal and refuse deletion.

    Its webhook secrets do NOT survive: they are credentials for paths that no
    longer exist, and the change log's tombstone snapshot deliberately doesn't
    carry them, so leaving the rows behind would only mean a recreated agent
    silently inheriting a secret nobody can see (docs/design/16).

    Args:
        name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentDefOut | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        name=name,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    name: str,
    *,
    client: AuthenticatedClient | Client,
) -> AgentDefOut | HTTPValidationError | None:
    """Delete Agent

     Delete an agent's definition. Its runs, memories and change log survive.

    Deleting is a write, so it logs one: a TOMBSTONE version whose snapshot is
    the definition as it stood at the moment of deletion, and whose
    `changed_via` is the normal label prefixed `delete:` (`delete:admin`,
    `delete:tool:agents_edit`). The prefix is the whole marker — snapshots stay
    uniformly parseable as definitions, with no synthetic keys inside them — and
    it means the log alone is enough to say who removed an agent and to
    recreate it. System agents are platform-internal and refuse deletion.

    Its webhook secrets do NOT survive: they are credentials for paths that no
    longer exist, and the change log's tombstone snapshot deliberately doesn't
    carry them, so leaving the rows behind would only mean a recreated agent
    silently inheriting a secret nobody can see (docs/design/16).

    Args:
        name (str):

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
        )
    ).parsed
