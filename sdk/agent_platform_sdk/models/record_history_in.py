from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="RecordHistoryIn")


@_attrs_define
class RecordHistoryIn:
    """
    Attributes:
        app (str): App id or name
        collection (str):
        id (str):
        before_version (int | None | Unset):
        limit (int | Unset):  Default: 50.
    """

    app: str
    collection: str
    id: str
    before_version: int | None | Unset = UNSET
    limit: int | Unset = 50

    def to_dict(self) -> dict[str, Any]:
        app = self.app

        collection = self.collection

        id = self.id

        before_version: int | None | Unset
        if isinstance(self.before_version, Unset):
            before_version = UNSET
        else:
            before_version = self.before_version

        limit = self.limit

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app": app,
                "collection": collection,
                "id": id,
            }
        )
        if before_version is not UNSET:
            field_dict["before_version"] = before_version
        if limit is not UNSET:
            field_dict["limit"] = limit

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        app = d.pop("app")

        collection = d.pop("collection")

        id = d.pop("id")

        def _parse_before_version(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        before_version = _parse_before_version(d.pop("before_version", UNSET))

        limit = d.pop("limit", UNSET)

        record_history_in = cls(
            app=app,
            collection=collection,
            id=id,
            before_version=before_version,
            limit=limit,
        )

        return record_history_in
