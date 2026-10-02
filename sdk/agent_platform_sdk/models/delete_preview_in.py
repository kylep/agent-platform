from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="DeletePreviewIn")


@_attrs_define
class DeletePreviewIn:
    """
    Attributes:
        app (str): App id or name
        collection (str):
        ids (list[str]):
    """

    app: str
    collection: str
    ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        app = self.app

        collection = self.collection

        ids = self.ids

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app": app,
                "collection": collection,
                "ids": ids,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        app = d.pop("app")

        collection = d.pop("collection")

        ids = cast(list[str], d.pop("ids"))

        delete_preview_in = cls(
            app=app,
            collection=collection,
            ids=ids,
        )

        return delete_preview_in
