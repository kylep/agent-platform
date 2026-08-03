from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.report_meta import ReportMeta
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    type_: None | str | Unset = UNSET,
    date_from: None | str | Unset = UNSET,
    date_to: None | str | Unset = UNSET,
    limit: int | Unset = 200,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_type_: None | str | Unset
    if isinstance(type_, Unset):
        json_type_ = UNSET
    else:
        json_type_ = type_
    params["type"] = json_type_

    json_date_from: None | str | Unset
    if isinstance(date_from, Unset):
        json_date_from = UNSET
    else:
        json_date_from = date_from
    params["date_from"] = json_date_from

    json_date_to: None | str | Unset
    if isinstance(date_to, Unset):
        json_date_to = UNSET
    else:
        json_date_to = date_to
    params["date_to"] = json_date_to

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/reports",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[ReportMeta] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = ReportMeta.from_dict(response_200_item_data)

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
) -> Response[HTTPValidationError | list[ReportMeta]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    type_: None | str | Unset = UNSET,
    date_from: None | str | Unset = UNSET,
    date_to: None | str | Unset = UNSET,
    limit: int | Unset = 200,
) -> Response[HTTPValidationError | list[ReportMeta]]:
    """List Reports

     Report metadata (no HTML) for calendars and lists, newest first. ISO
    date strings make the range filter lexicographic.

    Args:
        type_ (None | str | Unset):
        date_from (None | str | Unset):
        date_to (None | str | Unset):
        limit (int | Unset):  Default: 200.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[ReportMeta]]
    """

    kwargs = _get_kwargs(
        type_=type_,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    type_: None | str | Unset = UNSET,
    date_from: None | str | Unset = UNSET,
    date_to: None | str | Unset = UNSET,
    limit: int | Unset = 200,
) -> HTTPValidationError | list[ReportMeta] | None:
    """List Reports

     Report metadata (no HTML) for calendars and lists, newest first. ISO
    date strings make the range filter lexicographic.

    Args:
        type_ (None | str | Unset):
        date_from (None | str | Unset):
        date_to (None | str | Unset):
        limit (int | Unset):  Default: 200.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[ReportMeta]
    """

    return sync_detailed(
        client=client,
        type_=type_,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    type_: None | str | Unset = UNSET,
    date_from: None | str | Unset = UNSET,
    date_to: None | str | Unset = UNSET,
    limit: int | Unset = 200,
) -> Response[HTTPValidationError | list[ReportMeta]]:
    """List Reports

     Report metadata (no HTML) for calendars and lists, newest first. ISO
    date strings make the range filter lexicographic.

    Args:
        type_ (None | str | Unset):
        date_from (None | str | Unset):
        date_to (None | str | Unset):
        limit (int | Unset):  Default: 200.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[ReportMeta]]
    """

    kwargs = _get_kwargs(
        type_=type_,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    type_: None | str | Unset = UNSET,
    date_from: None | str | Unset = UNSET,
    date_to: None | str | Unset = UNSET,
    limit: int | Unset = 200,
) -> HTTPValidationError | list[ReportMeta] | None:
    """List Reports

     Report metadata (no HTML) for calendars and lists, newest first. ISO
    date strings make the range filter lexicographic.

    Args:
        type_ (None | str | Unset):
        date_from (None | str | Unset):
        date_to (None | str | Unset):
        limit (int | Unset):  Default: 200.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[ReportMeta]
    """

    return (
        await asyncio_detailed(
            client=client,
            type_=type_,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )
    ).parsed
