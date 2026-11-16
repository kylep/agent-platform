from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.figma_import_out import FigmaImportOut
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    import_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/ui-designer/imports/{import_id}".format(
            import_id=quote(str(import_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> FigmaImportOut | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = FigmaImportOut.from_dict(response.json())

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
) -> Response[FigmaImportOut | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    import_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[FigmaImportOut | HTTPValidationError]:
    """Get Figma Import

    Args:
        import_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[FigmaImportOut | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        import_id=import_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    import_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> FigmaImportOut | HTTPValidationError | None:
    """Get Figma Import

    Args:
        import_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        FigmaImportOut | HTTPValidationError
    """

    return sync_detailed(
        import_id=import_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    import_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[FigmaImportOut | HTTPValidationError]:
    """Get Figma Import

    Args:
        import_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[FigmaImportOut | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        import_id=import_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    import_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> FigmaImportOut | HTTPValidationError | None:
    """Get Figma Import

    Args:
        import_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        FigmaImportOut | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            import_id=import_id,
            client=client,
        )
    ).parsed
