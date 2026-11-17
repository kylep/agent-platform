from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.ui_motion_in_easing import UiMotionInEasing
from ..models.ui_motion_in_fps import UiMotionInFps
from ..types import UNSET, Unset

T = TypeVar("T", bound="UiMotionIn")


@_attrs_define
class UiMotionIn:
    """
    Attributes:
        from_node (str):
        to_node (str):
        version (int):
        duration_ms (int | Unset):  Default: 800.
        easing (UiMotionInEasing | Unset):  Default: UiMotionInEasing.EASE_IN_OUT.
        fps (UiMotionInFps | Unset):  Default: UiMotionInFps.VALUE_24.
        max_edge (int | Unset):  Default: 720.
        mode (None | str | Unset):
    """

    from_node: str
    to_node: str
    version: int
    duration_ms: int | Unset = 800
    easing: UiMotionInEasing | Unset = UiMotionInEasing.EASE_IN_OUT
    fps: UiMotionInFps | Unset = UiMotionInFps.VALUE_24
    max_edge: int | Unset = 720
    mode: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from_node = self.from_node

        to_node = self.to_node

        version = self.version

        duration_ms = self.duration_ms

        easing: str | Unset = UNSET
        if not isinstance(self.easing, Unset):
            easing = self.easing.value

        fps: int | Unset = UNSET
        if not isinstance(self.fps, Unset):
            fps = self.fps.value

        max_edge = self.max_edge

        mode: None | str | Unset
        if isinstance(self.mode, Unset):
            mode = UNSET
        else:
            mode = self.mode

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "from_node": from_node,
                "to_node": to_node,
                "version": version,
            }
        )
        if duration_ms is not UNSET:
            field_dict["duration_ms"] = duration_ms
        if easing is not UNSET:
            field_dict["easing"] = easing
        if fps is not UNSET:
            field_dict["fps"] = fps
        if max_edge is not UNSET:
            field_dict["max_edge"] = max_edge
        if mode is not UNSET:
            field_dict["mode"] = mode

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        from_node = d.pop("from_node")

        to_node = d.pop("to_node")

        version = d.pop("version")

        duration_ms = d.pop("duration_ms", UNSET)

        _easing = d.pop("easing", UNSET)
        easing: UiMotionInEasing | Unset
        if isinstance(_easing, Unset):
            easing = UNSET
        else:
            easing = UiMotionInEasing(_easing)

        _fps = d.pop("fps", UNSET)
        fps: UiMotionInFps | Unset
        if isinstance(_fps, Unset):
            fps = UNSET
        else:
            fps = UiMotionInFps(_fps)

        max_edge = d.pop("max_edge", UNSET)

        def _parse_mode(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        mode = _parse_mode(d.pop("mode", UNSET))

        ui_motion_in = cls(
            from_node=from_node,
            to_node=to_node,
            version=version,
            duration_ms=duration_ms,
            easing=easing,
            fps=fps,
            max_edge=max_edge,
            mode=mode,
        )

        return ui_motion_in
