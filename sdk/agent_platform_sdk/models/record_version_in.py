from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="RecordVersionIn")


@_attrs_define
class RecordVersionIn:
    """
    Attributes:
        app (str): App id or name
        collection (str):
        id (str):
        version (int):
    """

    app: str
    collection: str
    id: str
    version: int

    def to_dict(self) -> dict[str, Any]:
        app = self.app

        collection = self.collection

        id = self.id

        version = self.version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app": app,
                "collection": collection,
                "id": id,
                "version": version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        app = d.pop("app")

        collection = d.pop("collection")

        id = d.pop("id")

        version = d.pop("version")

        record_version_in = cls(
            app=app,
            collection=collection,
            id=id,
            version=version,
        )

        return record_version_in
