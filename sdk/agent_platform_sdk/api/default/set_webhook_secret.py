from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.webhook_secret_in import WebhookSecretIn
from ...models.webhook_secret_state import WebhookSecretState
from ...types import Response


def _get_kwargs(
    name: str,
    path: str,
    *,
    body: WebhookSecretIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/api/agents/{name}/webhooks/{path}/secret".format(
            name=quote(str(name), safe=""),
            path=quote(str(path), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | WebhookSecretState | None:
    if response.status_code == 200:
        response_200 = WebhookSecretState.from_dict(response.json())

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
) -> Response[HTTPValidationError | WebhookSecretState]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    name: str,
    path: str,
    *,
    client: AuthenticatedClient | Client,
    body: WebhookSecretIn,
) -> Response[HTTPValidationError | WebhookSecretState]:
    """Set Webhook Secret

     Set or rotate one webhook path's shared secret. Write-only: the response
    reports THAT a secret is set, never what it is, and nothing reads it back.
    Rotation replaces the stored digest in place, so the previous secret stops
    working the moment this returns.

    Args:
        name (str):
        path (str):
        body (WebhookSecretIn): Setting/rotating one webhook's shared secret (docs/design/16). Its
            own
            endpoint rather than a definition field, because the value must never take
            the definition's path through `agent_versions`. Write-only: no response
            model carries it back, and nothing reads it out again.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | WebhookSecretState]
    """

    kwargs = _get_kwargs(
        name=name,
        path=path,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    name: str,
    path: str,
    *,
    client: AuthenticatedClient | Client,
    body: WebhookSecretIn,
) -> HTTPValidationError | WebhookSecretState | None:
    """Set Webhook Secret

     Set or rotate one webhook path's shared secret. Write-only: the response
    reports THAT a secret is set, never what it is, and nothing reads it back.
    Rotation replaces the stored digest in place, so the previous secret stops
    working the moment this returns.

    Args:
        name (str):
        path (str):
        body (WebhookSecretIn): Setting/rotating one webhook's shared secret (docs/design/16). Its
            own
            endpoint rather than a definition field, because the value must never take
            the definition's path through `agent_versions`. Write-only: no response
            model carries it back, and nothing reads it out again.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | WebhookSecretState
    """

    return sync_detailed(
        name=name,
        path=path,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    name: str,
    path: str,
    *,
    client: AuthenticatedClient | Client,
    body: WebhookSecretIn,
) -> Response[HTTPValidationError | WebhookSecretState]:
    """Set Webhook Secret

     Set or rotate one webhook path's shared secret. Write-only: the response
    reports THAT a secret is set, never what it is, and nothing reads it back.
    Rotation replaces the stored digest in place, so the previous secret stops
    working the moment this returns.

    Args:
        name (str):
        path (str):
        body (WebhookSecretIn): Setting/rotating one webhook's shared secret (docs/design/16). Its
            own
            endpoint rather than a definition field, because the value must never take
            the definition's path through `agent_versions`. Write-only: no response
            model carries it back, and nothing reads it out again.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | WebhookSecretState]
    """

    kwargs = _get_kwargs(
        name=name,
        path=path,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    name: str,
    path: str,
    *,
    client: AuthenticatedClient | Client,
    body: WebhookSecretIn,
) -> HTTPValidationError | WebhookSecretState | None:
    """Set Webhook Secret

     Set or rotate one webhook path's shared secret. Write-only: the response
    reports THAT a secret is set, never what it is, and nothing reads it back.
    Rotation replaces the stored digest in place, so the previous secret stops
    working the moment this returns.

    Args:
        name (str):
        path (str):
        body (WebhookSecretIn): Setting/rotating one webhook's shared secret (docs/design/16). Its
            own
            endpoint rather than a definition field, because the value must never take
            the definition's path through `agent_versions`. Write-only: no response
            model carries it back, and nothing reads it out again.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | WebhookSecretState
    """

    return (
        await asyncio_detailed(
            name=name,
            path=path,
            client=client,
            body=body,
        )
    ).parsed
