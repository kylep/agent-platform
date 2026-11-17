from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.ui_motion_pose import UiMotionPose


T = TypeVar("T", bound="UiMotionLayer")


@_attrs_define
class UiMotionLayer:
    """
    Attributes:
        from_ (None | UiMotionPose):
        id (str):
        to (None | UiMotionPose):
    """

    from_: None | UiMotionPose
    id: str
    to: None | UiMotionPose

    def to_dict(self) -> dict[str, Any]:
        from ..models.ui_motion_pose import UiMotionPose

        from_: dict[str, Any] | None
        if isinstance(self.from_, UiMotionPose):
            from_ = self.from_.to_dict()
        else:
            from_ = self.from_

        id = self.id

        to: dict[str, Any] | None
        if isinstance(self.to, UiMotionPose):
            to = self.to.to_dict()
        else:
            to = self.to

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "from": from_,
                "id": id,
                "to": to,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_motion_pose import UiMotionPose

        d = dict(src_dict)

        def _parse_from_(data: object) -> None | UiMotionPose:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                from_type_0 = UiMotionPose.from_dict(data)

                return from_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UiMotionPose, data)

        from_ = _parse_from_(d.pop("from"))

        id = d.pop("id")

        def _parse_to(data: object) -> None | UiMotionPose:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                to_type_0 = UiMotionPose.from_dict(data)

                return to_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UiMotionPose, data)

        to = _parse_to(d.pop("to"))

        ui_motion_layer = cls(
            from_=from_,
            id=id,
            to=to,
        )

        return ui_motion_layer
