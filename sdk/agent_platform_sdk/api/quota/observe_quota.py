from http import HTTPStatus
from typing import Any, cast

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.observe_quota_quota_ignored import ObserveQuotaQuotaIgnored
from ...models.observe_quota_quota_observe_in import ObserveQuotaQuotaObserveIn
from ...models.quota import Quota
from ...types import Response


def _get_kwargs(
    *,
    body: ObserveQuotaQuotaObserveIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/internal/quota",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | ObserveQuotaQuotaIgnored | Quota | None:
    if response.status_code == 200:

        def _parse_response_200(data: object) -> ObserveQuotaQuotaIgnored | Quota:
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                response_200_type_0 = Quota.from_dict(data)

                return response_200_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            if not isinstance(data, dict):
                raise TypeError()
            response_200_quota_ignored = ObserveQuotaQuotaIgnored.from_dict(data)

            return response_200_quota_ignored

        response_200 = _parse_response_200(response.json())

        return response_200

    if response.status_code == 401:
        response_401 = cast(Any, None)
        return response_401

    if response.status_code == 413:
        response_413 = cast(Any, None)
        return response_413

    if response.status_code == 422:
        response_422 = cast(Any, None)
        return response_422

    if response.status_code == 503:
        response_503 = cast(Any, None)
        return response_503

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any | ObserveQuotaQuotaIgnored | Quota]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ObserveQuotaQuotaObserveIn,
) -> Response[Any | ObserveQuotaQuotaIgnored | Quota]:
    """Observe Quota

     The proxy's report — the hot path, and the one that costs nothing: every
    response it relays already carries these headers, so the snapshot is
    usually a side effect of work someone else was doing anyway.

    No session and no API key reach this route, by design: nginx presents the
    shared secret and nothing else. A missing server-side secret is a 503
    rather than an open door, and a wrong one is a 401 that does not say
    whether the header was absent or merely wrong.

    The body is read BY HAND, in that order, which is the whole reason this
    route does not declare a pydantic parameter. FastAPI would parse and
    validate the body before the handler runs at all — so an anonymous caller
    would get a 422 describing the schema of an endpoint they cannot use, and
    would have had a megabyte of their JSON parsed to earn it. Here the secret
    is checked against nothing but a header, then the body is read under a
    cap, and only then is it anybody's schema.

    Args:
        body (ObserveQuotaQuotaObserveIn): What the claude-proxy reports (docs/design/22): the
            response headers it
            just relayed, verbatim. Extra keys are tolerated — the proxy is a shell
            script's worth of nginx/njs and the contract has to survive it growing a
            field — and a body carrying no usage header at all is ignored, not an
            error, because most responses say nothing about usage.

            The bounds are here rather than left to the store because this is the one
            door on the platform that a session cannot open and an API key cannot
            open: whatever reaches it has already been trusted on a shared secret, so
            the shape is the only thing left to check.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ObserveQuotaQuotaIgnored | Quota]
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
    body: ObserveQuotaQuotaObserveIn,
) -> Any | ObserveQuotaQuotaIgnored | Quota | None:
    """Observe Quota

     The proxy's report — the hot path, and the one that costs nothing: every
    response it relays already carries these headers, so the snapshot is
    usually a side effect of work someone else was doing anyway.

    No session and no API key reach this route, by design: nginx presents the
    shared secret and nothing else. A missing server-side secret is a 503
    rather than an open door, and a wrong one is a 401 that does not say
    whether the header was absent or merely wrong.

    The body is read BY HAND, in that order, which is the whole reason this
    route does not declare a pydantic parameter. FastAPI would parse and
    validate the body before the handler runs at all — so an anonymous caller
    would get a 422 describing the schema of an endpoint they cannot use, and
    would have had a megabyte of their JSON parsed to earn it. Here the secret
    is checked against nothing but a header, then the body is read under a
    cap, and only then is it anybody's schema.

    Args:
        body (ObserveQuotaQuotaObserveIn): What the claude-proxy reports (docs/design/22): the
            response headers it
            just relayed, verbatim. Extra keys are tolerated — the proxy is a shell
            script's worth of nginx/njs and the contract has to survive it growing a
            field — and a body carrying no usage header at all is ignored, not an
            error, because most responses say nothing about usage.

            The bounds are here rather than left to the store because this is the one
            door on the platform that a session cannot open and an API key cannot
            open: whatever reaches it has already been trusted on a shared secret, so
            the shape is the only thing left to check.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ObserveQuotaQuotaIgnored | Quota
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ObserveQuotaQuotaObserveIn,
) -> Response[Any | ObserveQuotaQuotaIgnored | Quota]:
    """Observe Quota

     The proxy's report — the hot path, and the one that costs nothing: every
    response it relays already carries these headers, so the snapshot is
    usually a side effect of work someone else was doing anyway.

    No session and no API key reach this route, by design: nginx presents the
    shared secret and nothing else. A missing server-side secret is a 503
    rather than an open door, and a wrong one is a 401 that does not say
    whether the header was absent or merely wrong.

    The body is read BY HAND, in that order, which is the whole reason this
    route does not declare a pydantic parameter. FastAPI would parse and
    validate the body before the handler runs at all — so an anonymous caller
    would get a 422 describing the schema of an endpoint they cannot use, and
    would have had a megabyte of their JSON parsed to earn it. Here the secret
    is checked against nothing but a header, then the body is read under a
    cap, and only then is it anybody's schema.

    Args:
        body (ObserveQuotaQuotaObserveIn): What the claude-proxy reports (docs/design/22): the
            response headers it
            just relayed, verbatim. Extra keys are tolerated — the proxy is a shell
            script's worth of nginx/njs and the contract has to survive it growing a
            field — and a body carrying no usage header at all is ignored, not an
            error, because most responses say nothing about usage.

            The bounds are here rather than left to the store because this is the one
            door on the platform that a session cannot open and an API key cannot
            open: whatever reaches it has already been trusted on a shared secret, so
            the shape is the only thing left to check.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ObserveQuotaQuotaIgnored | Quota]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: ObserveQuotaQuotaObserveIn,
) -> Any | ObserveQuotaQuotaIgnored | Quota | None:
    """Observe Quota

     The proxy's report — the hot path, and the one that costs nothing: every
    response it relays already carries these headers, so the snapshot is
    usually a side effect of work someone else was doing anyway.

    No session and no API key reach this route, by design: nginx presents the
    shared secret and nothing else. A missing server-side secret is a 503
    rather than an open door, and a wrong one is a 401 that does not say
    whether the header was absent or merely wrong.

    The body is read BY HAND, in that order, which is the whole reason this
    route does not declare a pydantic parameter. FastAPI would parse and
    validate the body before the handler runs at all — so an anonymous caller
    would get a 422 describing the schema of an endpoint they cannot use, and
    would have had a megabyte of their JSON parsed to earn it. Here the secret
    is checked against nothing but a header, then the body is read under a
    cap, and only then is it anybody's schema.

    Args:
        body (ObserveQuotaQuotaObserveIn): What the claude-proxy reports (docs/design/22): the
            response headers it
            just relayed, verbatim. Extra keys are tolerated — the proxy is a shell
            script's worth of nginx/njs and the contract has to survive it growing a
            field — and a body carrying no usage header at all is ignored, not an
            error, because most responses say nothing about usage.

            The bounds are here rather than left to the store because this is the one
            door on the platform that a session cannot open and an API key cannot
            open: whatever reaches it has already been trusted on a shared secret, so
            the shape is the only thing left to check.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ObserveQuotaQuotaIgnored | Quota
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
