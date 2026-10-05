from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="StagingOpenIn")


@_attrs_define
class StagingOpenIn:
    """
    Attributes:
        app (str): App id or name
        collections (list[str]):
    """

    app: str
    collections: list[str]

    def to_dict(self) -> dict[str, Any]:
        app = self.app

        collections = self.collections

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app": app,
                "collections": collections,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        app = d.pop("app")

        collections = cast(list[str], d.pop("collections"))

        staging_open_in = cls(
            app=app,
            collections=collections,
        )

        return staging_open_in
