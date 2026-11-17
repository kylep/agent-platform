from __future__ import annotations

from collections.abc import Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    TypeVar,
    cast,
)

from attrs import define as _attrs_define

from ..models.ui_motion_preview_out_easing import UiMotionPreviewOutEasing
from ..models.ui_motion_preview_out_fps import UiMotionPreviewOutFps
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ui_motion_layer import UiMotionLayer
    from ..models.ui_motion_preview_out_assets import UiMotionPreviewOutAssets


T = TypeVar("T", bound="UiMotionPreviewOut")


@_attrs_define
class UiMotionPreviewOut:
    """
    Attributes:
        assets (UiMotionPreviewOutAssets):
        box (list[float]):
        duration_ms (int):
        easing (UiMotionPreviewOutEasing):
        fps (UiMotionPreviewOutFps):
        frames (int):
        from_image (str):
        height (int):
        layers (list[UiMotionLayer]):
        motion_version (Literal['motion-1']):
        renderer_version (str):
        to_image (str):
        width (int):
        mode (None | str | Unset):
        warnings (list[str] | Unset):
    """

    assets: UiMotionPreviewOutAssets
    box: list[float]
    duration_ms: int
    easing: UiMotionPreviewOutEasing
    fps: UiMotionPreviewOutFps
    frames: int
    from_image: str
    height: int
    layers: list[UiMotionLayer]
    motion_version: Literal["motion-1"]
    renderer_version: str
    to_image: str
    width: int
    mode: None | str | Unset = UNSET
    warnings: list[str] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        assets = self.assets.to_dict()

        box = self.box

        duration_ms = self.duration_ms

        easing = self.easing.value

        fps = self.fps.value

        frames = self.frames

        from_image = self.from_image

        height = self.height

        layers = []
        for layers_item_data in self.layers:
            layers_item = layers_item_data.to_dict()
            layers.append(layers_item)

        motion_version = self.motion_version

        renderer_version = self.renderer_version

        to_image = self.to_image

        width = self.width

        mode: None | str | Unset
        if isinstance(self.mode, Unset):
            mode = UNSET
        else:
            mode = self.mode

        warnings: list[str] | Unset = UNSET
        if not isinstance(self.warnings, Unset):
            warnings = self.warnings

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "assets": assets,
                "box": box,
                "duration_ms": duration_ms,
                "easing": easing,
                "fps": fps,
                "frames": frames,
                "from_image": from_image,
                "height": height,
                "layers": layers,
                "motion_version": motion_version,
                "renderer_version": renderer_version,
                "to_image": to_image,
                "width": width,
            }
        )
        if mode is not UNSET:
            field_dict["mode"] = mode
        if warnings is not UNSET:
            field_dict["warnings"] = warnings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_motion_layer import UiMotionLayer
        from ..models.ui_motion_preview_out_assets import UiMotionPreviewOutAssets

        d = dict(src_dict)
        assets = UiMotionPreviewOutAssets.from_dict(d.pop("assets"))

        box = cast(list[float], d.pop("box"))

        duration_ms = d.pop("duration_ms")

        easing = UiMotionPreviewOutEasing(d.pop("easing"))

        fps = UiMotionPreviewOutFps(d.pop("fps"))

        frames = d.pop("frames")

        from_image = d.pop("from_image")

        height = d.pop("height")

        layers = []
        _layers = d.pop("layers")
        for layers_item_data in _layers:
            layers_item = UiMotionLayer.from_dict(layers_item_data)

            layers.append(layers_item)

        motion_version = cast(Literal["motion-1"], d.pop("motion_version"))
        if motion_version != "motion-1":
            raise ValueError(
                f"motion_version must match const 'motion-1', got '{motion_version}'"
            )

        renderer_version = d.pop("renderer_version")

        to_image = d.pop("to_image")

        width = d.pop("width")

        def _parse_mode(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        mode = _parse_mode(d.pop("mode", UNSET))

        warnings = cast(list[str], d.pop("warnings", UNSET))

        ui_motion_preview_out = cls(
            assets=assets,
            box=box,
            duration_ms=duration_ms,
            easing=easing,
            fps=fps,
            frames=frames,
            from_image=from_image,
            height=height,
            layers=layers,
            motion_version=motion_version,
            renderer_version=renderer_version,
            to_image=to_image,
            width=width,
            mode=mode,
            warnings=warnings,
        )

        return ui_motion_preview_out
