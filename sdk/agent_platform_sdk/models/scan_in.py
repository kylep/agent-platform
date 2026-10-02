from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.scan_in_filter_item import ScanInFilterItem
    from ..models.scan_in_sort_item import ScanInSortItem


T = TypeVar("T", bound="ScanIn")


@_attrs_define
class ScanIn:
    """
    Attributes:
        fields (list[str]):
        role (str):
        cursor (None | str | Unset):
        filter_ (list[ScanInFilterItem] | Unset):
        limit (int | Unset):  Default: 1000.
        sort (list[ScanInSortItem] | Unset):
    """

    fields: list[str]
    role: str
    cursor: None | str | Unset = UNSET
    filter_: list[ScanInFilterItem] | Unset = UNSET
    limit: int | Unset = 1000
    sort: list[ScanInSortItem] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        fields = self.fields

        role = self.role

        cursor: None | str | Unset
        if isinstance(self.cursor, Unset):
            cursor = UNSET
        else:
            cursor = self.cursor

        filter_: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.filter_, Unset):
            filter_ = []
            for filter_item_data in self.filter_:
                filter_item = filter_item_data.to_dict()
                filter_.append(filter_item)

        limit = self.limit

        sort: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.sort, Unset):
            sort = []
            for sort_item_data in self.sort:
                sort_item = sort_item_data.to_dict()
                sort.append(sort_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "fields": fields,
                "role": role,
            }
        )
        if cursor is not UNSET:
            field_dict["cursor"] = cursor
        if filter_ is not UNSET:
            field_dict["filter"] = filter_
        if limit is not UNSET:
            field_dict["limit"] = limit
        if sort is not UNSET:
            field_dict["sort"] = sort

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.scan_in_filter_item import ScanInFilterItem
        from ..models.scan_in_sort_item import ScanInSortItem

        d = dict(src_dict)
        fields = cast(list[str], d.pop("fields"))

        role = d.pop("role")

        def _parse_cursor(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        cursor = _parse_cursor(d.pop("cursor", UNSET))

        _filter_ = d.pop("filter", UNSET)
        filter_: list[ScanInFilterItem] | Unset = UNSET
        if _filter_ is not UNSET:
            filter_ = []
            for filter_item_data in _filter_:
                filter_item = ScanInFilterItem.from_dict(filter_item_data)

                filter_.append(filter_item)

        limit = d.pop("limit", UNSET)

        _sort = d.pop("sort", UNSET)
        sort: list[ScanInSortItem] | Unset = UNSET
        if _sort is not UNSET:
            sort = []
            for sort_item_data in _sort:
                sort_item = ScanInSortItem.from_dict(sort_item_data)

                sort.append(sort_item)

        scan_in = cls(
            fields=fields,
            role=role,
            cursor=cursor,
            filter_=filter_,
            limit=limit,
            sort=sort,
        )

        return scan_in
