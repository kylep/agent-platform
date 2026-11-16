from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.ui_doc_diff_out import UiDocDiffOut
from ...types import UNSET, Response


def _get_kwargs(
    slug: str,
    *,
    from_: int,
    to: int,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["from"] = from_

    params["to"] = to

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/ui-designer/docs/{slug}/diff".format(
            slug=quote(str(slug), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | UiDocDiffOut | None:
    if response.status_code == 200:
        response_200 = UiDocDiffOut.from_dict(response.json())

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
) -> Response[HTTPValidationError | UiDocDiffOut]:
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
    from_: int,
    to: int,
) -> Response[HTTPValidationError | UiDocDiffOut]:
    """Diff Ui Document

     The writes between two version numbers (`from` exclusive, `to` inclusive),
    ops as applied. Every number is resolvable, folded or not (design 46 section 10).

    Args:
        slug (str):
        from_ (int):
        to (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiDocDiffOut]
    """

    kwargs = _get_kwargs(
        slug=slug,
        from_=from_,
        to=to,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    from_: int,
    to: int,
) -> HTTPValidationError | UiDocDiffOut | None:
    """Diff Ui Document

     The writes between two version numbers (`from` exclusive, `to` inclusive),
    ops as applied. Every number is resolvable, folded or not (design 46 section 10).

    Args:
        slug (str):
        from_ (int):
        to (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiDocDiffOut
    """

    return sync_detailed(
        slug=slug,
        client=client,
        from_=from_,
        to=to,
    ).parsed


async def asyncio_detailed(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    from_: int,
    to: int,
) -> Response[HTTPValidationError | UiDocDiffOut]:
    """Diff Ui Document

     The writes between two version numbers (`from` exclusive, `to` inclusive),
    ops as applied. Every number is resolvable, folded or not (design 46 section 10).

    Args:
        slug (str):
        from_ (int):
        to (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UiDocDiffOut]
    """

    kwargs = _get_kwargs(
        slug=slug,
        from_=from_,
        to=to,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    from_: int,
    to: int,
) -> HTTPValidationError | UiDocDiffOut | None:
    """Diff Ui Document

     The writes between two version numbers (`from` exclusive, `to` inclusive),
    ops as applied. Every number is resolvable, folded or not (design 46 section 10).

    Args:
        slug (str):
        from_ (int):
        to (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UiDocDiffOut
    """

    return (
        await asyncio_detailed(
            slug=slug,
            client=client,
            from_=from_,
            to=to,
        )
    ).parsed
