from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.kafka_health import KafkaHealth
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/health/kafka",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> KafkaHealth | None:
    if response.status_code == 200:
        response_200 = KafkaHealth.from_dict(response.json())

        return response_200

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[KafkaHealth]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[KafkaHealth]:
    """Kafka Health

     Broker liveness + expected-topic presence, a best-effort dispatcher lag,
    and a DB-derived run backlog (queued/active/dlq). Powers the dashboard's
    Kafka health panel; deeper metrics wait for the M05 observability pass.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[KafkaHealth]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> KafkaHealth | None:
    """Kafka Health

     Broker liveness + expected-topic presence, a best-effort dispatcher lag,
    and a DB-derived run backlog (queued/active/dlq). Powers the dashboard's
    Kafka health panel; deeper metrics wait for the M05 observability pass.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        KafkaHealth
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[KafkaHealth]:
    """Kafka Health

     Broker liveness + expected-topic presence, a best-effort dispatcher lag,
    and a DB-derived run backlog (queued/active/dlq). Powers the dashboard's
    Kafka health panel; deeper metrics wait for the M05 observability pass.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[KafkaHealth]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> KafkaHealth | None:
    """Kafka Health

     Broker liveness + expected-topic presence, a best-effort dispatcher lag,
    and a DB-derived run backlog (queued/active/dlq). Powers the dashboard's
    Kafka health panel; deeper metrics wait for the M05 observability pass.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        KafkaHealth
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
