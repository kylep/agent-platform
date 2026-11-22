from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.lint_ui_document_severity_type_0 import LintUiDocumentSeverityType0
from ...models.ui_lint_out import UiLintOut
from ...types import UNSET, Response, Unset


def _get_kwargs(
    slug: str,
    *,
    version: int,
    node: None | str | Unset = UNSET,
    mode: None | str | Unset = UNSET,
    all_modes: bool | Unset = False,
    severity: LintUiDocumentSeverityType0 | None | Unset = UNSET,
    limit: int | Unset = 100,
    cursor: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["version"] = version

    json_node: None | str | Unset
    if isinstance(node, Unset):
        json_node = UNSET
    else:
        json_node = node
    params["node"] = json_node

    json_mode: None | str | Unset
    if isinstance(mode, Unset):
        json_mode = UNSET
    else:
        json_mode = mode
    params["mode"] = json_mode

    params["all_modes"] = all_modes

    json_severity: None | str | Unset
    if isinstance(severity, Unset):
        json_severity = UNSET
    elif isinstance(severity, LintUiDocumentSeverityType0):
        json_severity = severity.value
    else:
        json_severity = severity
    params["severity"] = json_severity

    params["limit"] = limit

    json_cursor: None | str | Unset
    if isinstance(cursor, Unset):
        json_cursor = UNSET
    else:
        json_cursor = cursor
    params["cursor"] = json_cursor

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/ui-designer/docs/{slug}/lint".format(
            slug=quote(str(slug), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | UiLintOut | None:
    if response.status_code == 200:
        response_200 = UiLintOut.from_dict(response.json())

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
) -> Response[HTTPValidationError | UiLintOut]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    version: int,
    node: None | str | Unset = UNSET,
    mode: None | str | Unset = UNSET,
    all_modes: bool | Unset = False,
    severity: LintUiDocumentSeverityType0 | None | Unset = UNSET,
    limit: int | Unset = 100,
    cursor: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | UiLintOut]:
    """Lint Ui Document

     Deterministic findings for a retained version; pagination never follows latest.

    Args:
        slug (str):
        version (int):
        node (None | str | Unset):
        mode (None | str | Unset):
        all_modes (bool | Unset):  Default: False.
        severity (LintUiDocumentSeverityType0 | None | Unset):
        limit (int | Unset):  Default: 100.
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiLintOut]
    """

    kwargs = _get_kwargs(
        slug=slug,
        version=version,
        node=node,
        mode=mode,
        all_modes=all_modes,
        severity=severity,
        limit=limit,
        cursor=cursor,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    version: int,
    node: None | str | Unset = UNSET,
    mode: None | str | Unset = UNSET,
    all_modes: bool | Unset = False,
    severity: LintUiDocumentSeverityType0 | None | Unset = UNSET,
    limit: int | Unset = 100,
    cursor: None | str | Unset = UNSET,
) -> HTTPValidationError | UiLintOut | None:
    """Lint Ui Document

     Deterministic findings for a retained version; pagination never follows latest.

    Args:
        slug (str):
        version (int):
        node (None | str | Unset):
        mode (None | str | Unset):
        all_modes (bool | Unset):  Default: False.
        severity (LintUiDocumentSeverityType0 | None | Unset):
        limit (int | Unset):  Default: 100.
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiLintOut
    """

    return sync_detailed(
        slug=slug,
        client=client,
        version=version,
        node=node,
        mode=mode,
        all_modes=all_modes,
        severity=severity,
        limit=limit,
        cursor=cursor,
    ).parsed


async def asyncio_detailed(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    version: int,
    node: None | str | Unset = UNSET,
    mode: None | str | Unset = UNSET,
    all_modes: bool | Unset = False,
    severity: LintUiDocumentSeverityType0 | None | Unset = UNSET,
    limit: int | Unset = 100,
    cursor: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | UiLintOut]:
    """Lint Ui Document

     Deterministic findings for a retained version; pagination never follows latest.

    Args:
        slug (str):
        version (int):
        node (None | str | Unset):
        mode (None | str | Unset):
        all_modes (bool | Unset):  Default: False.
        severity (LintUiDocumentSeverityType0 | None | Unset):
        limit (int | Unset):  Default: 100.
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiLintOut]
    """

    kwargs = _get_kwargs(
        slug=slug,
        version=version,
        node=node,
        mode=mode,
        all_modes=all_modes,
        severity=severity,
        limit=limit,
        cursor=cursor,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    version: int,
    node: None | str | Unset = UNSET,
    mode: None | str | Unset = UNSET,
    all_modes: bool | Unset = False,
    severity: LintUiDocumentSeverityType0 | None | Unset = UNSET,
    limit: int | Unset = 100,
    cursor: None | str | Unset = UNSET,
) -> HTTPValidationError | UiLintOut | None:
    """Lint Ui Document

     Deterministic findings for a retained version; pagination never follows latest.

    Args:
        slug (str):
        version (int):
        node (None | str | Unset):
        mode (None | str | Unset):
        all_modes (bool | Unset):  Default: False.
        severity (LintUiDocumentSeverityType0 | None | Unset):
        limit (int | Unset):  Default: 100.
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiLintOut
    """

    return (
        await asyncio_detailed(
            slug=slug,
            client=client,
            version=version,
            node=node,
            mode=mode,
            all_modes=all_modes,
            severity=severity,
            limit=limit,
            cursor=cursor,
        )
    ).parsed
