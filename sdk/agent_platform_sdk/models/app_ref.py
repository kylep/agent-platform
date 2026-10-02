from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="AppRef")


@_attrs_define
class AppRef:
    """
    Attributes:
        app (str): App id or name
    """

    app: str

    def to_dict(self) -> dict[str, Any]:
        app = self.app

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app": app,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        app = d.pop("app")

        app_ref = cls(
            app=app,
        )

        return app_ref
