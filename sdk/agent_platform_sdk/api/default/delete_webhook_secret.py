from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.webhook_secret_state import WebhookSecretState
from ...types import Response


def _get_kwargs(
    name: str,
    path: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/api/agents/{name}/webhooks/{path}/secret".format(
            name=quote(str(name), safe=""),
            path=quote(str(path), safe=""),
        ),
    }

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
) -> Response[HTTPValidationError | WebhookSecretState]:
    r"""Delete Webhook Secret

     Remove one webhook path's secret. Idempotent — the caller asked for \"no
    secret on this path\", and that is the state either way. A path still in
    `secret` mode with no secret is the fail-closed case: it rejects callers
    rather than falling back to the platform key alone.

    Args:
        name (str):
        path (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | WebhookSecretState]
    """

    kwargs = _get_kwargs(
        name=name,
        path=path,
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
) -> HTTPValidationError | WebhookSecretState | None:
    r"""Delete Webhook Secret

     Remove one webhook path's secret. Idempotent — the caller asked for \"no
    secret on this path\", and that is the state either way. A path still in
    `secret` mode with no secret is the fail-closed case: it rejects callers
    rather than falling back to the platform key alone.

    Args:
        name (str):
        path (str):

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
    ).parsed


async def asyncio_detailed(
    name: str,
    path: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | WebhookSecretState]:
    r"""Delete Webhook Secret

     Remove one webhook path's secret. Idempotent — the caller asked for \"no
    secret on this path\", and that is the state either way. A path still in
    `secret` mode with no secret is the fail-closed case: it rejects callers
    rather than falling back to the platform key alone.

    Args:
        name (str):
        path (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | WebhookSecretState]
    """

    kwargs = _get_kwargs(
        name=name,
        path=path,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    name: str,
    path: str,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | WebhookSecretState | None:
    r"""Delete Webhook Secret

     Remove one webhook path's secret. Idempotent — the caller asked for \"no
    secret on this path\", and that is the state either way. A path still in
    `secret` mode with no secret is the fail-closed case: it rejects callers
    rather than falling back to the platform key alone.

    Args:
        name (str):
        path (str):

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
        )
    ).parsed
