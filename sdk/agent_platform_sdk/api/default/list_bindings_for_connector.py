from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.relay_binding_ref import RelayBindingRef
from ...types import UNSET, Response


def _get_kwargs(
    *,
    connector: str,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["connector"] = connector

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/relay/bindings",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[RelayBindingRef] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = RelayBindingRef.from_dict(response_200_item_data)

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
) -> Response[HTTPValidationError | list[RelayBindingRef]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    connector: str,
) -> Response[HTTPValidationError | list[RelayBindingRef]]:
    """List Bindings For Connector

     Every endpoint this connector owns. A bridge asks the platform which
    rooms it is responsible for rather than being told in its environment: a
    binding made in the UI has to reach it without a redeploy, and the
    connector holds no state of its own worth trusting.

    Endpoint kind is explicit: the connector hydrates channel mirrors and
    assistant threads into separate maps, so a restart can resume a known
    thread without trying to attach a channel webhook to it.

    Args:
        connector (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[RelayBindingRef]]
    """

    kwargs = _get_kwargs(
        connector=connector,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    connector: str,
) -> HTTPValidationError | list[RelayBindingRef] | None:
    """List Bindings For Connector

     Every endpoint this connector owns. A bridge asks the platform which
    rooms it is responsible for rather than being told in its environment: a
    binding made in the UI has to reach it without a redeploy, and the
    connector holds no state of its own worth trusting.

    Endpoint kind is explicit: the connector hydrates channel mirrors and
    assistant threads into separate maps, so a restart can resume a known
    thread without trying to attach a channel webhook to it.

    Args:
        connector (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[RelayBindingRef]
    """

    return sync_detailed(
        client=client,
        connector=connector,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    connector: str,
) -> Response[HTTPValidationError | list[RelayBindingRef]]:
    """List Bindings For Connector

     Every endpoint this connector owns. A bridge asks the platform which
    rooms it is responsible for rather than being told in its environment: a
    binding made in the UI has to reach it without a redeploy, and the
    connector holds no state of its own worth trusting.

    Endpoint kind is explicit: the connector hydrates channel mirrors and
    assistant threads into separate maps, so a restart can resume a known
    thread without trying to attach a channel webhook to it.

    Args:
        connector (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[RelayBindingRef]]
    """

    kwargs = _get_kwargs(
        connector=connector,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    connector: str,
) -> HTTPValidationError | list[RelayBindingRef] | None:
    """List Bindings For Connector

     Every endpoint this connector owns. A bridge asks the platform which
    rooms it is responsible for rather than being told in its environment: a
    binding made in the UI has to reach it without a redeploy, and the
    connector holds no state of its own worth trusting.

    Endpoint kind is explicit: the connector hydrates channel mirrors and
    assistant threads into separate maps, so a restart can resume a known
    thread without trying to attach a channel webhook to it.

    Args:
        connector (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[RelayBindingRef]
    """

    return (
        await asyncio_detailed(
            client=client,
            connector=connector,
        )
    ).parsed
