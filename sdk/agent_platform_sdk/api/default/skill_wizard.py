from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.edit_dispatch import EditDispatch
from ...models.http_validation_error import HTTPValidationError
from ...models.skill_wizard_in import SkillWizardIn
from ...types import Response


def _get_kwargs(
    *,
    body: SkillWizardIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/skills/new",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> EditDispatch | HTTPValidationError | None:
    if response.status_code == 202:
        response_202 = EditDispatch.from_dict(response.json())

        return response_202

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[EditDispatch | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: SkillWizardIn,
) -> Response[EditDispatch | HTTPValidationError]:
    """Skill Wizard

     The New-Skill wizard: turn interview answers into a platform-coder run
    that AUTHORS the skill (and, when a new credential is involved, scaffolds
    its `secrets/<name>/secret.yaml`). The result lands as a pull request under
    Changes — agent-authored, human-reviewed.

    Args:
        body (SkillWizardIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EditDispatch | HTTPValidationError]
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
    body: SkillWizardIn,
) -> EditDispatch | HTTPValidationError | None:
    """Skill Wizard

     The New-Skill wizard: turn interview answers into a platform-coder run
    that AUTHORS the skill (and, when a new credential is involved, scaffolds
    its `secrets/<name>/secret.yaml`). The result lands as a pull request under
    Changes — agent-authored, human-reviewed.

    Args:
        body (SkillWizardIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EditDispatch | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: SkillWizardIn,
) -> Response[EditDispatch | HTTPValidationError]:
    """Skill Wizard

     The New-Skill wizard: turn interview answers into a platform-coder run
    that AUTHORS the skill (and, when a new credential is involved, scaffolds
    its `secrets/<name>/secret.yaml`). The result lands as a pull request under
    Changes — agent-authored, human-reviewed.

    Args:
        body (SkillWizardIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EditDispatch | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: SkillWizardIn,
) -> EditDispatch | HTTPValidationError | None:
    """Skill Wizard

     The New-Skill wizard: turn interview answers into a platform-coder run
    that AUTHORS the skill (and, when a new credential is involved, scaffolds
    its `secrets/<name>/secret.yaml`). The result lands as a pull request under
    Changes — agent-authored, human-reviewed.

    Args:
        body (SkillWizardIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EditDispatch | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
