from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="RecordDeleteIn")


@_attrs_define
class RecordDeleteIn:
    """
    Attributes:
        app (str): App id or name
        collection (str):
        id (str):
        request_id (str):
        expected_version (int | None | Unset):
    """

    app: str
    collection: str
    id: str
    request_id: str
    expected_version: int | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        app = self.app

        collection = self.collection

        id = self.id

        request_id = self.request_id

        expected_version: int | None | Unset
        if isinstance(self.expected_version, Unset):
            expected_version = UNSET
        else:
            expected_version = self.expected_version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app": app,
                "collection": collection,
                "id": id,
                "request_id": request_id,
            }
        )
        if expected_version is not UNSET:
            field_dict["expected_version"] = expected_version

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        app = d.pop("app")

        collection = d.pop("collection")

        id = d.pop("id")

        request_id = d.pop("request_id")

        def _parse_expected_version(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        expected_version = _parse_expected_version(d.pop("expected_version", UNSET))

        record_delete_in = cls(
            app=app,
            collection=collection,
            id=id,
            request_id=request_id,
            expected_version=expected_version,
        )

        return record_delete_in
