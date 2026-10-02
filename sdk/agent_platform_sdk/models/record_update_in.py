from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.record_update_in_values import RecordUpdateInValues


T = TypeVar("T", bound="RecordUpdateIn")


@_attrs_define
class RecordUpdateIn:
    """
    Attributes:
        app (str): App id or name
        collection (str):
        expected_version (int):
        id (str):
        request_id (str):
        values (RecordUpdateInValues):
    """

    app: str
    collection: str
    expected_version: int
    id: str
    request_id: str
    values: RecordUpdateInValues

    def to_dict(self) -> dict[str, Any]:
        app = self.app

        collection = self.collection

        expected_version = self.expected_version

        id = self.id

        request_id = self.request_id

        values = self.values.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app": app,
                "collection": collection,
                "expected_version": expected_version,
                "id": id,
                "request_id": request_id,
                "values": values,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.record_update_in_values import RecordUpdateInValues

        d = dict(src_dict)
        app = d.pop("app")

        collection = d.pop("collection")

        expected_version = d.pop("expected_version")

        id = d.pop("id")

        request_id = d.pop("request_id")

        values = RecordUpdateInValues.from_dict(d.pop("values"))

        record_update_in = cls(
            app=app,
            collection=collection,
            expected_version=expected_version,
            id=id,
            request_id=request_id,
            values=values,
        )

        return record_update_in
