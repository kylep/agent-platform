from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.artifact_view import ArtifactView
from ...models.generate_in import GenerateIn
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    *,
    body: GenerateIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/artifacts/generate",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ArtifactView | HTTPValidationError | None:
    if response.status_code == 201:
        response_201 = ArtifactView.from_dict(response.json())

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
) -> Response[ArtifactView | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: GenerateIn,
) -> Response[ArtifactView | HTTPValidationError]:
    """Generate Artifact

     Generate one image and keep it (docs/design/23): the ONE place a
    generation happens is `image_gen_service.generate`; this is its door.
    Synchronous — the executor's timeout plus a grace, so a caller waits up
    to ~210 s — and the response is the artifact. The owner is the caller;
    an agent's generation carries its run, and a token without one is
    refused before anything is spent.

    Behind `image_gen` itself for an agent, not the either-grant fence the
    store's routes share: `artifacts` is default-granted to every agent so
    that any of them can keep a file, and this is the one door that spends
    money. Humans are bounded by their role, as everywhere else.

    Args:
        body (GenerateIn): `model` defaults to the registry's default; `size` or `aspect` is
            bridged by the tool when the model takes the other; `reference_ids` are
            artifacts the caller can read, images only, at most four.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ArtifactView | HTTPValidationError]
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
    body: GenerateIn,
) -> ArtifactView | HTTPValidationError | None:
    """Generate Artifact

     Generate one image and keep it (docs/design/23): the ONE place a
    generation happens is `image_gen_service.generate`; this is its door.
    Synchronous — the executor's timeout plus a grace, so a caller waits up
    to ~210 s — and the response is the artifact. The owner is the caller;
    an agent's generation carries its run, and a token without one is
    refused before anything is spent.

    Behind `image_gen` itself for an agent, not the either-grant fence the
    store's routes share: `artifacts` is default-granted to every agent so
    that any of them can keep a file, and this is the one door that spends
    money. Humans are bounded by their role, as everywhere else.

    Args:
        body (GenerateIn): `model` defaults to the registry's default; `size` or `aspect` is
            bridged by the tool when the model takes the other; `reference_ids` are
            artifacts the caller can read, images only, at most four.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ArtifactView | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: GenerateIn,
) -> Response[ArtifactView | HTTPValidationError]:
    """Generate Artifact

     Generate one image and keep it (docs/design/23): the ONE place a
    generation happens is `image_gen_service.generate`; this is its door.
    Synchronous — the executor's timeout plus a grace, so a caller waits up
    to ~210 s — and the response is the artifact. The owner is the caller;
    an agent's generation carries its run, and a token without one is
    refused before anything is spent.

    Behind `image_gen` itself for an agent, not the either-grant fence the
    store's routes share: `artifacts` is default-granted to every agent so
    that any of them can keep a file, and this is the one door that spends
    money. Humans are bounded by their role, as everywhere else.

    Args:
        body (GenerateIn): `model` defaults to the registry's default; `size` or `aspect` is
            bridged by the tool when the model takes the other; `reference_ids` are
            artifacts the caller can read, images only, at most four.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ArtifactView | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: GenerateIn,
) -> ArtifactView | HTTPValidationError | None:
    """Generate Artifact

     Generate one image and keep it (docs/design/23): the ONE place a
    generation happens is `image_gen_service.generate`; this is its door.
    Synchronous — the executor's timeout plus a grace, so a caller waits up
    to ~210 s — and the response is the artifact. The owner is the caller;
    an agent's generation carries its run, and a token without one is
    refused before anything is spent.

    Behind `image_gen` itself for an agent, not the either-grant fence the
    store's routes share: `artifacts` is default-granted to every agent so
    that any of them can keep a file, and this is the one door that spends
    money. Humans are bounded by their role, as everywhere else.

    Args:
        body (GenerateIn): `model` defaults to the registry's default; `size` or `aspect` is
            bridged by the tool when the model takes the other; `reference_ids` are
            artifacts the caller can read, images only, at most four.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ArtifactView | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
