from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="StagingFinishIn")


@_attrs_define
class StagingFinishIn:
    """
    Attributes:
        app (str): App id or name
        set_id (str):
    """

    app: str
    set_id: str

    def to_dict(self) -> dict[str, Any]:
        app = self.app

        set_id = self.set_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app": app,
                "set_id": set_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        app = d.pop("app")

        set_id = d.pop("set_id")

        staging_finish_in = cls(
            app=app,
            set_id=set_id,
        )

        return staging_finish_in
