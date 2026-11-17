from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.ui_motion_export_in_easing import UiMotionExportInEasing
from ..models.ui_motion_export_in_format import UiMotionExportInFormat
from ..models.ui_motion_export_in_fps import UiMotionExportInFps
from ..types import UNSET, Unset

T = TypeVar("T", bound="UiMotionExportIn")


@_attrs_define
class UiMotionExportIn:
    """
    Attributes:
        from_node (str):
        request_id (str):
        to_node (str):
        version (int):
        duration_ms (int | Unset):  Default: 800.
        easing (UiMotionExportInEasing | Unset):  Default: UiMotionExportInEasing.EASE_IN_OUT.
        format_ (UiMotionExportInFormat | Unset):  Default: UiMotionExportInFormat.MP4.
        fps (UiMotionExportInFps | Unset):  Default: UiMotionExportInFps.VALUE_24.
        max_edge (int | Unset):  Default: 720.
        mode (None | str | Unset):
    """

    from_node: str
    request_id: str
    to_node: str
    version: int
    duration_ms: int | Unset = 800
    easing: UiMotionExportInEasing | Unset = UiMotionExportInEasing.EASE_IN_OUT
    format_: UiMotionExportInFormat | Unset = UiMotionExportInFormat.MP4
    fps: UiMotionExportInFps | Unset = UiMotionExportInFps.VALUE_24
    max_edge: int | Unset = 720
    mode: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from_node = self.from_node

        request_id = self.request_id

        to_node = self.to_node

        version = self.version

        duration_ms = self.duration_ms

        easing: str | Unset = UNSET
        if not isinstance(self.easing, Unset):
            easing = self.easing.value

        format_: str | Unset = UNSET
        if not isinstance(self.format_, Unset):
            format_ = self.format_.value

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
                "request_id": request_id,
                "to_node": to_node,
                "version": version,
            }
        )
        if duration_ms is not UNSET:
            field_dict["duration_ms"] = duration_ms
        if easing is not UNSET:
            field_dict["easing"] = easing
        if format_ is not UNSET:
            field_dict["format"] = format_
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

        request_id = d.pop("request_id")

        to_node = d.pop("to_node")

        version = d.pop("version")

        duration_ms = d.pop("duration_ms", UNSET)

        _easing = d.pop("easing", UNSET)
        easing: UiMotionExportInEasing | Unset
        if isinstance(_easing, Unset):
            easing = UNSET
        else:
            easing = UiMotionExportInEasing(_easing)

        _format_ = d.pop("format", UNSET)
        format_: UiMotionExportInFormat | Unset
        if isinstance(_format_, Unset):
            format_ = UNSET
        else:
            format_ = UiMotionExportInFormat(_format_)

        _fps = d.pop("fps", UNSET)
        fps: UiMotionExportInFps | Unset
        if isinstance(_fps, Unset):
            fps = UNSET
        else:
            fps = UiMotionExportInFps(_fps)

        max_edge = d.pop("max_edge", UNSET)

        def _parse_mode(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        mode = _parse_mode(d.pop("mode", UNSET))

        ui_motion_export_in = cls(
            from_node=from_node,
            request_id=request_id,
            to_node=to_node,
            version=version,
            duration_ms=duration_ms,
            easing=easing,
            format_=format_,
            fps=fps,
            max_edge=max_edge,
            mode=mode,
        )

        return ui_motion_export_in
