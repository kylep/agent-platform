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
    path: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/webhooks/{path}".format(
            path=quote(str(path), safe=""),
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
    path: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | RunAccepted]:
    """Webhook

     External async trigger: an operator+ caller fires the agent that
    DECLARES `{path}` in its entrypoints.yaml `webhooks:` list (docs/design/10)
    — an undeclared path doesn't exist, so an agent can't be webhook-fired
    unless its definition opted in. The request body becomes prompt context.
    Event-sourced: we validate the command, then produce a `run.requested`
    event to `run.inbound`; the ingest consumer materializes the run. The
    pre-assigned id is returned so the caller can follow the run.

    Args:
        path (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | RunAccepted]
    """

    kwargs = _get_kwargs(
        path=path,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    path: str,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | RunAccepted | None:
    """Webhook

     External async trigger: an operator+ caller fires the agent that
    DECLARES `{path}` in its entrypoints.yaml `webhooks:` list (docs/design/10)
    — an undeclared path doesn't exist, so an agent can't be webhook-fired
    unless its definition opted in. The request body becomes prompt context.
    Event-sourced: we validate the command, then produce a `run.requested`
    event to `run.inbound`; the ingest consumer materializes the run. The
    pre-assigned id is returned so the caller can follow the run.

    Args:
        path (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | RunAccepted
    """

    return sync_detailed(
        path=path,
        client=client,
    ).parsed


async def asyncio_detailed(
    path: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | RunAccepted]:
    """Webhook

     External async trigger: an operator+ caller fires the agent that
    DECLARES `{path}` in its entrypoints.yaml `webhooks:` list (docs/design/10)
    — an undeclared path doesn't exist, so an agent can't be webhook-fired
    unless its definition opted in. The request body becomes prompt context.
    Event-sourced: we validate the command, then produce a `run.requested`
    event to `run.inbound`; the ingest consumer materializes the run. The
    pre-assigned id is returned so the caller can follow the run.

    Args:
        path (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | RunAccepted]
    """

    kwargs = _get_kwargs(
        path=path,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    path: str,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | RunAccepted | None:
    """Webhook

     External async trigger: an operator+ caller fires the agent that
    DECLARES `{path}` in its entrypoints.yaml `webhooks:` list (docs/design/10)
    — an undeclared path doesn't exist, so an agent can't be webhook-fired
    unless its definition opted in. The request body becomes prompt context.
    Event-sourced: we validate the command, then produce a `run.requested`
    event to `run.inbound`; the ingest consumer materializes the run. The
    pre-assigned id is returned so the caller can follow the run.

    Args:
        path (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | RunAccepted
    """

    return (
        await asyncio_detailed(
            path=path,
            client=client,
        )
    ).parsed
