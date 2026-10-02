from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="RefreshIn")


@_attrs_define
class RefreshIn:
    """
    Attributes:
        app (str): App id or name
        view (str):
    """

    app: str
    view: str

    def to_dict(self) -> dict[str, Any]:
        app = self.app

        view = self.view

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app": app,
                "view": view,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        app = d.pop("app")

        view = d.pop("view")

        refresh_in = cls(
            app=app,
            view=view,
        )

        return refresh_in
