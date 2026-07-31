from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.edit_result import EditResult
from ...models.http_validation_error import HTTPValidationError
from ...models.skill_quick_edit_in import SkillQuickEditIn
from ...types import Response


def _get_kwargs(
    name: str,
    *,
    body: SkillQuickEditIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/skills/{name}/quick-edit".format(
            name=quote(str(name), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> EditResult | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = EditResult.from_dict(response.json())

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
) -> Response[EditResult | HTTPValidationError]:
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
    body: SkillQuickEditIn,
) -> Response[EditResult | HTTPValidationError]:
    """Skill Quick Edit

     Deterministic edit that skips the agent: writes the exact SKILL.md the
    caller supplies and ALWAYS opens a pull request on the skill's
    deterministic branch (`coder/skill-{name}`) — the same
    save→pending-change→review contract as the agent definition editor.

    Args:
        name (str):
        body (SkillQuickEditIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EditResult | HTTPValidationError]
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
    body: SkillQuickEditIn,
) -> EditResult | HTTPValidationError | None:
    """Skill Quick Edit

     Deterministic edit that skips the agent: writes the exact SKILL.md the
    caller supplies and ALWAYS opens a pull request on the skill's
    deterministic branch (`coder/skill-{name}`) — the same
    save→pending-change→review contract as the agent definition editor.

    Args:
        name (str):
        body (SkillQuickEditIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EditResult | HTTPValidationError
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
    body: SkillQuickEditIn,
) -> Response[EditResult | HTTPValidationError]:
    """Skill Quick Edit

     Deterministic edit that skips the agent: writes the exact SKILL.md the
    caller supplies and ALWAYS opens a pull request on the skill's
    deterministic branch (`coder/skill-{name}`) — the same
    save→pending-change→review contract as the agent definition editor.

    Args:
        name (str):
        body (SkillQuickEditIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EditResult | HTTPValidationError]
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
    body: SkillQuickEditIn,
) -> EditResult | HTTPValidationError | None:
    """Skill Quick Edit

     Deterministic edit that skips the agent: writes the exact SKILL.md the
    caller supplies and ALWAYS opens a pull request on the skill's
    deterministic branch (`coder/skill-{name}`) — the same
    save→pending-change→review contract as the agent definition editor.

    Args:
        name (str):
        body (SkillQuickEditIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EditResult | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            name=name,
            client=client,
            body=body,
        )
    ).parsed
