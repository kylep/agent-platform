from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.artifact_view import ArtifactView
from ...models.create_artifact_files_body import CreateArtifactFilesBody
from ...models.create_artifact_json_artifact_in import CreateArtifactJsonArtifactIn
from ...types import UNSET, Response


def _get_kwargs(
    *,
    body: CreateArtifactJsonArtifactIn | CreateArtifactFilesBody | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/artifacts",
    }

    if isinstance(body, CreateArtifactJsonArtifactIn):
        _kwargs["json"] = body.to_dict()

        headers["Content-Type"] = "application/json"
    if isinstance(body, CreateArtifactFilesBody):
        _kwargs["files"] = body.to_multipart()

        headers["Content-Type"] = "multipart/form-data; boundary=+++"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ArtifactView | None:
    if response.status_code == 201:
        response_201 = ArtifactView.from_dict(response.json())

        return response_201

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ArtifactView]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: CreateArtifactJsonArtifactIn | CreateArtifactFilesBody | Unset = UNSET,
) -> Response[ArtifactView]:
    """Create Artifact

     Save bytes as a new artifact, as multipart (`file`, `name?`, `tags?`,
    `meta?`) or JSON (`ArtifactIn`). The owner is the caller; an agent's
    write carries its run. A parent named in `meta.parent_id` that is an
    existing image makes the new artifact `derived`.

    Args:
        body (CreateArtifactJsonArtifactIn): The JSON create: text, or bytes as base64, exactly
            one of them. `mime`
            is a CLAIM the store honours only for a few text types over bytes that
            decode. `source` may not say `generated` — that is the generate route's
            word for something it paid for.
        body (CreateArtifactFilesBody):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ArtifactView]
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
    body: CreateArtifactJsonArtifactIn | CreateArtifactFilesBody | Unset = UNSET,
) -> ArtifactView | None:
    """Create Artifact

     Save bytes as a new artifact, as multipart (`file`, `name?`, `tags?`,
    `meta?`) or JSON (`ArtifactIn`). The owner is the caller; an agent's
    write carries its run. A parent named in `meta.parent_id` that is an
    existing image makes the new artifact `derived`.

    Args:
        body (CreateArtifactJsonArtifactIn): The JSON create: text, or bytes as base64, exactly
            one of them. `mime`
            is a CLAIM the store honours only for a few text types over bytes that
            decode. `source` may not say `generated` — that is the generate route's
            word for something it paid for.
        body (CreateArtifactFilesBody):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ArtifactView
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: CreateArtifactJsonArtifactIn | CreateArtifactFilesBody | Unset = UNSET,
) -> Response[ArtifactView]:
    """Create Artifact

     Save bytes as a new artifact, as multipart (`file`, `name?`, `tags?`,
    `meta?`) or JSON (`ArtifactIn`). The owner is the caller; an agent's
    write carries its run. A parent named in `meta.parent_id` that is an
    existing image makes the new artifact `derived`.

    Args:
        body (CreateArtifactJsonArtifactIn): The JSON create: text, or bytes as base64, exactly
            one of them. `mime`
            is a CLAIM the store honours only for a few text types over bytes that
            decode. `source` may not say `generated` — that is the generate route's
            word for something it paid for.
        body (CreateArtifactFilesBody):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ArtifactView]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: CreateArtifactJsonArtifactIn | CreateArtifactFilesBody | Unset = UNSET,
) -> ArtifactView | None:
    """Create Artifact

     Save bytes as a new artifact, as multipart (`file`, `name?`, `tags?`,
    `meta?`) or JSON (`ArtifactIn`). The owner is the caller; an agent's
    write carries its run. A parent named in `meta.parent_id` that is an
    existing image makes the new artifact `derived`.

    Args:
        body (CreateArtifactJsonArtifactIn): The JSON create: text, or bytes as base64, exactly
            one of them. `mime`
            is a CLAIM the store honours only for a few text types over bytes that
            decode. `source` may not say `generated` — that is the generate route's
            word for something it paid for.
        body (CreateArtifactFilesBody):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ArtifactView
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
