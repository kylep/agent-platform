from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="UiVisualDiffRegion")


@_attrs_define
class UiVisualDiffRegion:
    """
    Attributes:
        h (float):
        node_ids (list[str]):
        w (float):
        x (float):
        y (float):
    """

    h: float
    node_ids: list[str]
    w: float
    x: float
    y: float
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        h = self.h

        node_ids = self.node_ids

        w = self.w

        x = self.x

        y = self.y

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "h": h,
                "node_ids": node_ids,
                "w": w,
                "x": x,
                "y": y,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        h = d.pop("h")

        node_ids = cast(list[str], d.pop("node_ids"))

        w = d.pop("w")

        x = d.pop("x")

        y = d.pop("y")

        ui_visual_diff_region = cls(
            h=h,
            node_ids=node_ids,
            w=w,
            x=x,
            y=y,
        )

        ui_visual_diff_region.additional_properties = d
        return ui_visual_diff_region

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
