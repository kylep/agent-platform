from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.query_in_params import QueryInParams


T = TypeVar("T", bound="QueryIn")


@_attrs_define
class QueryIn:
    """
    Attributes:
        app (str): App id or name
        view (str):
        cursor (None | str | Unset):
        limit (int | None | Unset):
        params (QueryInParams | Unset):
    """

    app: str
    view: str
    cursor: None | str | Unset = UNSET
    limit: int | None | Unset = UNSET
    params: QueryInParams | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        app = self.app

        view = self.view

        cursor: None | str | Unset
        if isinstance(self.cursor, Unset):
            cursor = UNSET
        else:
            cursor = self.cursor

        limit: int | None | Unset
        if isinstance(self.limit, Unset):
            limit = UNSET
        else:
            limit = self.limit

        params: dict[str, Any] | Unset = UNSET
        if not isinstance(self.params, Unset):
            params = self.params.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app": app,
                "view": view,
            }
        )
        if cursor is not UNSET:
            field_dict["cursor"] = cursor
        if limit is not UNSET:
            field_dict["limit"] = limit
        if params is not UNSET:
            field_dict["params"] = params

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.query_in_params import QueryInParams

        d = dict(src_dict)
        app = d.pop("app")

        view = d.pop("view")

        def _parse_cursor(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        cursor = _parse_cursor(d.pop("cursor", UNSET))

        def _parse_limit(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        limit = _parse_limit(d.pop("limit", UNSET))

        _params = d.pop("params", UNSET)
        params: QueryInParams | Unset
        if isinstance(_params, Unset):
            params = UNSET
        else:
            params = QueryInParams.from_dict(_params)

        query_in = cls(
            app=app,
            view=view,
            cursor=cursor,
            limit=limit,
            params=params,
        )

        return query_in
