from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.secret_access_view import SecretAccessView
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    run_id: None | str | Unset = UNSET,
    secret: None | str | Unset = UNSET,
    agent: None | str | Unset = UNSET,
    limit: int | Unset = 100,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_run_id: None | str | Unset
    if isinstance(run_id, Unset):
        json_run_id = UNSET
    else:
        json_run_id = run_id
    params["run_id"] = json_run_id

    json_secret: None | str | Unset
    if isinstance(secret, Unset):
        json_secret = UNSET
    else:
        json_secret = secret
    params["secret"] = json_secret

    json_agent: None | str | Unset
    if isinstance(agent, Unset):
        json_agent = UNSET
    else:
        json_agent = agent
    params["agent"] = json_agent

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/audit/secret-access",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[SecretAccessView] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = SecretAccessView.from_dict(response_200_item_data)

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
) -> Response[HTTPValidationError | list[SecretAccessView]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    run_id: None | str | Unset = UNSET,
    secret: None | str | Unset = UNSET,
    agent: None | str | Unset = UNSET,
    limit: int | Unset = 100,
) -> Response[HTTPValidationError | list[SecretAccessView]]:
    """Secret Access

     Audit trail of which k8s secrets each run's pod was granted. Filter by
    run_id, secret, or agent. Admin-only (secret names are sensitive).

    Args:
        run_id (None | str | Unset):
        secret (None | str | Unset):
        agent (None | str | Unset):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[SecretAccessView]]
    """

    kwargs = _get_kwargs(
        run_id=run_id,
        secret=secret,
        agent=agent,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    run_id: None | str | Unset = UNSET,
    secret: None | str | Unset = UNSET,
    agent: None | str | Unset = UNSET,
    limit: int | Unset = 100,
) -> HTTPValidationError | list[SecretAccessView] | None:
    """Secret Access

     Audit trail of which k8s secrets each run's pod was granted. Filter by
    run_id, secret, or agent. Admin-only (secret names are sensitive).

    Args:
        run_id (None | str | Unset):
        secret (None | str | Unset):
        agent (None | str | Unset):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[SecretAccessView]
    """

    return sync_detailed(
        client=client,
        run_id=run_id,
        secret=secret,
        agent=agent,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    run_id: None | str | Unset = UNSET,
    secret: None | str | Unset = UNSET,
    agent: None | str | Unset = UNSET,
    limit: int | Unset = 100,
) -> Response[HTTPValidationError | list[SecretAccessView]]:
    """Secret Access

     Audit trail of which k8s secrets each run's pod was granted. Filter by
    run_id, secret, or agent. Admin-only (secret names are sensitive).

    Args:
        run_id (None | str | Unset):
        secret (None | str | Unset):
        agent (None | str | Unset):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[SecretAccessView]]
    """

    kwargs = _get_kwargs(
        run_id=run_id,
        secret=secret,
        agent=agent,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    run_id: None | str | Unset = UNSET,
    secret: None | str | Unset = UNSET,
    agent: None | str | Unset = UNSET,
    limit: int | Unset = 100,
) -> HTTPValidationError | list[SecretAccessView] | None:
    """Secret Access

     Audit trail of which k8s secrets each run's pod was granted. Filter by
    run_id, secret, or agent. Admin-only (secret names are sensitive).

    Args:
        run_id (None | str | Unset):
        secret (None | str | Unset):
        agent (None | str | Unset):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[SecretAccessView]
    """

    return (
        await asyncio_detailed(
            client=client,
            run_id=run_id,
            secret=secret,
            agent=agent,
            limit=limit,
        )
    ).parsed
