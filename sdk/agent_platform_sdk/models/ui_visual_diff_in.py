from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="UiVisualDiffIn")


@_attrs_define
class UiVisualDiffIn:
    """
    Attributes:
        from_version (int):
        to_version (int):
        mode (None | str | Unset):
        node (None | str | Unset):
        scale (float | Unset):  Default: 1.0.
        tolerance (int | Unset):  Default: 0.
    """

    from_version: int
    to_version: int
    mode: None | str | Unset = UNSET
    node: None | str | Unset = UNSET
    scale: float | Unset = 1.0
    tolerance: int | Unset = 0

    def to_dict(self) -> dict[str, Any]:
        from_version = self.from_version

        to_version = self.to_version

        mode: None | str | Unset
        if isinstance(self.mode, Unset):
            mode = UNSET
        else:
            mode = self.mode

        node: None | str | Unset
        if isinstance(self.node, Unset):
            node = UNSET
        else:
            node = self.node

        scale = self.scale

        tolerance = self.tolerance

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "from_version": from_version,
                "to_version": to_version,
            }
        )
        if mode is not UNSET:
            field_dict["mode"] = mode
        if node is not UNSET:
            field_dict["node"] = node
        if scale is not UNSET:
            field_dict["scale"] = scale
        if tolerance is not UNSET:
            field_dict["tolerance"] = tolerance

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        from_version = d.pop("from_version")

        to_version = d.pop("to_version")

        def _parse_mode(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        mode = _parse_mode(d.pop("mode", UNSET))

        def _parse_node(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        node = _parse_node(d.pop("node", UNSET))

        scale = d.pop("scale", UNSET)

        tolerance = d.pop("tolerance", UNSET)

        ui_visual_diff_in = cls(
            from_version=from_version,
            to_version=to_version,
            mode=mode,
            node=node,
            scale=scale,
            tolerance=tolerance,
        )

        return ui_visual_diff_in
