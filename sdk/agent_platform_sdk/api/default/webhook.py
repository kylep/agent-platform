from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.run_accepted import RunAccepted
from ...types import Response


def _get_kwargs(
    agent: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/webhooks/{agent}".format(
            agent=quote(str(agent), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | RunAccepted | None:
    if response.status_code == 202:
        response_202 = RunAccepted.from_dict(response.json())

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
) -> Response[HTTPValidationError | RunAccepted]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    agent: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | RunAccepted]:
    """Webhook

     External async trigger: an operator+ caller fires `{agent}` with the
    request body as prompt context. This is **event-sourced** — we validate the
    command, then produce a `run.requested` event to `run.inbound`; the ingest
    consumer materializes the run. The pre-assigned id is returned so the caller
    can follow the run once it lands.

    Args:
        agent (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | RunAccepted]
    """

    kwargs = _get_kwargs(
        agent=agent,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    agent: str,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | RunAccepted | None:
    """Webhook

     External async trigger: an operator+ caller fires `{agent}` with the
    request body as prompt context. This is **event-sourced** — we validate the
    command, then produce a `run.requested` event to `run.inbound`; the ingest
    consumer materializes the run. The pre-assigned id is returned so the caller
    can follow the run once it lands.

    Args:
        agent (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | RunAccepted
    """

    return sync_detailed(
        agent=agent,
        client=client,
    ).parsed


async def asyncio_detailed(
    agent: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | RunAccepted]:
    """Webhook

     External async trigger: an operator+ caller fires `{agent}` with the
    request body as prompt context. This is **event-sourced** — we validate the
    command, then produce a `run.requested` event to `run.inbound`; the ingest
    consumer materializes the run. The pre-assigned id is returned so the caller
    can follow the run once it lands.

    Args:
        agent (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | RunAccepted]
    """

    kwargs = _get_kwargs(
        agent=agent,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    agent: str,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | RunAccepted | None:
    """Webhook

     External async trigger: an operator+ caller fires `{agent}` with the
    request body as prompt context. This is **event-sourced** — we validate the
    command, then produce a `run.requested` event to `run.inbound`; the ingest
    consumer materializes the run. The pre-assigned id is returned so the caller
    can follow the run once it lands.

    Args:
        agent (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | RunAccepted
    """

    return (
        await asyncio_detailed(
            agent=agent,
            client=client,
        )
    ).parsed
