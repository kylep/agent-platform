from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.record_create_in_values import RecordCreateInValues


T = TypeVar("T", bound="RecordCreateIn")


@_attrs_define
class RecordCreateIn:
    """
    Attributes:
        app (str): App id or name
        collection (str):
        request_id (str):
        values (RecordCreateInValues):
    """

    app: str
    collection: str
    request_id: str
    values: RecordCreateInValues

    def to_dict(self) -> dict[str, Any]:
        app = self.app

        collection = self.collection

        request_id = self.request_id

        values = self.values.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app": app,
                "collection": collection,
                "request_id": request_id,
                "values": values,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.record_create_in_values import RecordCreateInValues

        d = dict(src_dict)
        app = d.pop("app")

        collection = d.pop("collection")

        request_id = d.pop("request_id")

        values = RecordCreateInValues.from_dict(d.pop("values"))

        record_create_in = cls(
            app=app,
            collection=collection,
            request_id=request_id,
            values=values,
        )

        return record_create_in
