from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.publish_out import PublishOut
from ...models.publish_run_publish_in import PublishRunPublishIn
from ...types import Response


def _get_kwargs(
    run_id: str,
    *,
    body: PublishRunPublishIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/runs/{run_id}/publish".format(
            run_id=quote(str(run_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | PublishOut | None:
    if response.status_code == 201:
        response_201 = PublishOut.from_dict(response.json())

        return response_201

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[HTTPValidationError | PublishOut]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    run_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PublishRunPublishIn,
) -> Response[HTTPValidationError | PublishOut]:
    """Publish Run

     The one door code leaves a dev pod through. The nonce is checked before
    the body is touched; the body is bounded on the wire before any parser
    sees it (413), then the bundle is decoded and checked against
    `publish_max_bytes` again; everything else — ancestry, paths, policy,
    push, PR, ticket, card, envelope — is `workbench.publish`'s. 201 when the
    head landed, 200 when the same head was already the remote tip, 409 when
    the remote disagrees, 422 when the policy refuses.

    Args:
        run_id (str):
        body (PublishRunPublishIn): What `services/runner/workbench.py::finalize` POSTs. `verify`
            and
            `notes_md` are untrusted: the route hands them to the publish service,
            which shape-checks the one and strips the other.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | PublishOut]
    """

    kwargs = _get_kwargs(
        run_id=run_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    run_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PublishRunPublishIn,
) -> HTTPValidationError | PublishOut | None:
    """Publish Run

     The one door code leaves a dev pod through. The nonce is checked before
    the body is touched; the body is bounded on the wire before any parser
    sees it (413), then the bundle is decoded and checked against
    `publish_max_bytes` again; everything else — ancestry, paths, policy,
    push, PR, ticket, card, envelope — is `workbench.publish`'s. 201 when the
    head landed, 200 when the same head was already the remote tip, 409 when
    the remote disagrees, 422 when the policy refuses.

    Args:
        run_id (str):
        body (PublishRunPublishIn): What `services/runner/workbench.py::finalize` POSTs. `verify`
            and
            `notes_md` are untrusted: the route hands them to the publish service,
            which shape-checks the one and strips the other.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | PublishOut
    """

    return sync_detailed(
        run_id=run_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    run_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PublishRunPublishIn,
) -> Response[HTTPValidationError | PublishOut]:
    """Publish Run

     The one door code leaves a dev pod through. The nonce is checked before
    the body is touched; the body is bounded on the wire before any parser
    sees it (413), then the bundle is decoded and checked against
    `publish_max_bytes` again; everything else — ancestry, paths, policy,
    push, PR, ticket, card, envelope — is `workbench.publish`'s. 201 when the
    head landed, 200 when the same head was already the remote tip, 409 when
    the remote disagrees, 422 when the policy refuses.

    Args:
        run_id (str):
        body (PublishRunPublishIn): What `services/runner/workbench.py::finalize` POSTs. `verify`
            and
            `notes_md` are untrusted: the route hands them to the publish service,
            which shape-checks the one and strips the other.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | PublishOut]
    """

    kwargs = _get_kwargs(
        run_id=run_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    run_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PublishRunPublishIn,
) -> HTTPValidationError | PublishOut | None:
    """Publish Run

     The one door code leaves a dev pod through. The nonce is checked before
    the body is touched; the body is bounded on the wire before any parser
    sees it (413), then the bundle is decoded and checked against
    `publish_max_bytes` again; everything else — ancestry, paths, policy,
    push, PR, ticket, card, envelope — is `workbench.publish`'s. 201 when the
    head landed, 200 when the same head was already the remote tip, 409 when
    the remote disagrees, 422 when the policy refuses.

    Args:
        run_id (str):
        body (PublishRunPublishIn): What `services/runner/workbench.py::finalize` POSTs. `verify`
            and
            `notes_md` are untrusted: the route hands them to the publish service,
            which shape-checks the one and strips the other.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | PublishOut
    """

    return (
        await asyncio_detailed(
            run_id=run_id,
            client=client,
            body=body,
        )
    ).parsed
