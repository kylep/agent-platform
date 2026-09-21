from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.artifact_view import ArtifactView
from ...models.codex_generated_image import CodexGeneratedImage
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    run_id: str,
    *,
    body: CodexGeneratedImage,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/runs/{run_id}/generated-images".format(
            run_id=quote(str(run_id), safe=""),
        ),
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
    run_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: CodexGeneratedImage,
) -> Response[ArtifactView | HTTPValidationError]:
    """Keep Codex Generated Image

     Ingest a built-in ImageGen file from its own trusted runner.

    The session credential is tied to this run, and the immutable Run prompt
    carries the Studio's owner/provenance spec. The model never chooses who
    owns the bytes or whether they count as generated.

    Args:
        run_id (str):
        body (CodexGeneratedImage):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ArtifactView | HTTPValidationError]
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
    body: CodexGeneratedImage,
) -> ArtifactView | HTTPValidationError | None:
    """Keep Codex Generated Image

     Ingest a built-in ImageGen file from its own trusted runner.

    The session credential is tied to this run, and the immutable Run prompt
    carries the Studio's owner/provenance spec. The model never chooses who
    owns the bytes or whether they count as generated.

    Args:
        run_id (str):
        body (CodexGeneratedImage):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ArtifactView | HTTPValidationError
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
    body: CodexGeneratedImage,
) -> Response[ArtifactView | HTTPValidationError]:
    """Keep Codex Generated Image

     Ingest a built-in ImageGen file from its own trusted runner.

    The session credential is tied to this run, and the immutable Run prompt
    carries the Studio's owner/provenance spec. The model never chooses who
    owns the bytes or whether they count as generated.

    Args:
        run_id (str):
        body (CodexGeneratedImage):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ArtifactView | HTTPValidationError]
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
    body: CodexGeneratedImage,
) -> ArtifactView | HTTPValidationError | None:
    """Keep Codex Generated Image

     Ingest a built-in ImageGen file from its own trusted runner.

    The session credential is tied to this run, and the immutable Run prompt
    carries the Studio's owner/provenance spec. The model never chooses who
    owns the bytes or whether they count as generated.

    Args:
        run_id (str):
        body (CodexGeneratedImage):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ArtifactView | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            run_id=run_id,
            client=client,
            body=body,
        )
    ).parsed
