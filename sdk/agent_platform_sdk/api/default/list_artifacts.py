from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.artifact_view import ArtifactView
from ...models.http_validation_error import HTTPValidationError
from ...models.list_artifacts_kind_type_0 import ListArtifactsKindType0
from ...models.list_artifacts_source_type_0 import ListArtifactsSourceType0
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    kind: ListArtifactsKindType0 | None | Unset = UNSET,
    owner: None | str | Unset = UNSET,
    source: ListArtifactsSourceType0 | None | Unset = UNSET,
    q: None | str | Unset = UNSET,
    tag: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    before: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_kind: None | str | Unset
    if isinstance(kind, Unset):
        json_kind = UNSET
    elif isinstance(kind, ListArtifactsKindType0):
        json_kind = kind.value
    else:
        json_kind = kind
    params["kind"] = json_kind

    json_owner: None | str | Unset
    if isinstance(owner, Unset):
        json_owner = UNSET
    else:
        json_owner = owner
    params["owner"] = json_owner

    json_source: None | str | Unset
    if isinstance(source, Unset):
        json_source = UNSET
    elif isinstance(source, ListArtifactsSourceType0):
        json_source = source.value
    else:
        json_source = source
    params["source"] = json_source

    json_q: None | str | Unset
    if isinstance(q, Unset):
        json_q = UNSET
    else:
        json_q = q
    params["q"] = json_q

    json_tag: None | str | Unset
    if isinstance(tag, Unset):
        json_tag = UNSET
    else:
        json_tag = tag
    params["tag"] = json_tag

    params["limit"] = limit

    json_before: None | str | Unset
    if isinstance(before, Unset):
        json_before = UNSET
    else:
        json_before = before
    params["before"] = json_before

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/artifacts",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[ArtifactView] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = ArtifactView.from_dict(response_200_item_data)

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
) -> Response[HTTPValidationError | list[ArtifactView]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    kind: ListArtifactsKindType0 | None | Unset = UNSET,
    owner: None | str | Unset = UNSET,
    source: ListArtifactsSourceType0 | None | Unset = UNSET,
    q: None | str | Unset = UNSET,
    tag: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    before: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | list[ArtifactView]]:
    """List Artifacts

     Metadata only, newest first, keyset-paged from `before` (an artifact
    id). Never the bytes and never the thumb: those are the two routes below.

    Args:
        kind (ListArtifactsKindType0 | None | Unset):
        owner (None | str | Unset):
        source (ListArtifactsSourceType0 | None | Unset):
        q (None | str | Unset):
        tag (None | str | Unset):
        limit (int | Unset):  Default: 50.
        before (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[ArtifactView]]
    """

    kwargs = _get_kwargs(
        kind=kind,
        owner=owner,
        source=source,
        q=q,
        tag=tag,
        limit=limit,
        before=before,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    kind: ListArtifactsKindType0 | None | Unset = UNSET,
    owner: None | str | Unset = UNSET,
    source: ListArtifactsSourceType0 | None | Unset = UNSET,
    q: None | str | Unset = UNSET,
    tag: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    before: None | str | Unset = UNSET,
) -> HTTPValidationError | list[ArtifactView] | None:
    """List Artifacts

     Metadata only, newest first, keyset-paged from `before` (an artifact
    id). Never the bytes and never the thumb: those are the two routes below.

    Args:
        kind (ListArtifactsKindType0 | None | Unset):
        owner (None | str | Unset):
        source (ListArtifactsSourceType0 | None | Unset):
        q (None | str | Unset):
        tag (None | str | Unset):
        limit (int | Unset):  Default: 50.
        before (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[ArtifactView]
    """

    return sync_detailed(
        client=client,
        kind=kind,
        owner=owner,
        source=source,
        q=q,
        tag=tag,
        limit=limit,
        before=before,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    kind: ListArtifactsKindType0 | None | Unset = UNSET,
    owner: None | str | Unset = UNSET,
    source: ListArtifactsSourceType0 | None | Unset = UNSET,
    q: None | str | Unset = UNSET,
    tag: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    before: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | list[ArtifactView]]:
    """List Artifacts

     Metadata only, newest first, keyset-paged from `before` (an artifact
    id). Never the bytes and never the thumb: those are the two routes below.

    Args:
        kind (ListArtifactsKindType0 | None | Unset):
        owner (None | str | Unset):
        source (ListArtifactsSourceType0 | None | Unset):
        q (None | str | Unset):
        tag (None | str | Unset):
        limit (int | Unset):  Default: 50.
        before (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[ArtifactView]]
    """

    kwargs = _get_kwargs(
        kind=kind,
        owner=owner,
        source=source,
        q=q,
        tag=tag,
        limit=limit,
        before=before,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    kind: ListArtifactsKindType0 | None | Unset = UNSET,
    owner: None | str | Unset = UNSET,
    source: ListArtifactsSourceType0 | None | Unset = UNSET,
    q: None | str | Unset = UNSET,
    tag: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    before: None | str | Unset = UNSET,
) -> HTTPValidationError | list[ArtifactView] | None:
    """List Artifacts

     Metadata only, newest first, keyset-paged from `before` (an artifact
    id). Never the bytes and never the thumb: those are the two routes below.

    Args:
        kind (ListArtifactsKindType0 | None | Unset):
        owner (None | str | Unset):
        source (ListArtifactsSourceType0 | None | Unset):
        q (None | str | Unset):
        tag (None | str | Unset):
        limit (int | Unset):  Default: 50.
        before (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[ArtifactView]
    """

    return (
        await asyncio_detailed(
            client=client,
            kind=kind,
            owner=owner,
            source=source,
            q=q,
            tag=tag,
            limit=limit,
            before=before,
        )
    ).parsed
