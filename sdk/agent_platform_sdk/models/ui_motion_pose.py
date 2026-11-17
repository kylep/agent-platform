from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="UiMotionPose")


@_attrs_define
class UiMotionPose:
    """
    Attributes:
        asset (str):
        h (float):
        opacity (float):
        rotate (float):
        w (float):
        x (float):
        y (float):
    """

    asset: str
    h: float
    opacity: float
    rotate: float
    w: float
    x: float
    y: float

    def to_dict(self) -> dict[str, Any]:
        asset = self.asset

        h = self.h

        opacity = self.opacity

        rotate = self.rotate

        w = self.w

        x = self.x

        y = self.y

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "asset": asset,
                "h": h,
                "opacity": opacity,
                "rotate": rotate,
                "w": w,
                "x": x,
                "y": y,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        asset = d.pop("asset")

        h = d.pop("h")

        opacity = d.pop("opacity")

        rotate = d.pop("rotate")

        w = d.pop("w")

        x = d.pop("x")

        y = d.pop("y")

        ui_motion_pose = cls(
            asset=asset,
            h=h,
            opacity=opacity,
            rotate=rotate,
            w=w,
            x=x,
            y=y,
        )

        return ui_motion_pose
