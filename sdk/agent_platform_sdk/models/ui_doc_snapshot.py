from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ui_doc_snapshot_boxes_type_0 import UiDocSnapshotBoxesType0


T = TypeVar("T", bound="UiDocSnapshot")


@_attrs_define
class UiDocSnapshot:
    """
    Attributes:
        data (str):
        format_ (str):
        height (int):
        mime (str):
        width (int):
        boxes (None | UiDocSnapshotBoxesType0 | Unset):
        cached (bool | Unset):  Default: False.
        node (None | str | Unset):
    """

    data: str
    format_: str
    height: int
    mime: str
    width: int
    boxes: None | UiDocSnapshotBoxesType0 | Unset = UNSET
    cached: bool | Unset = False
    node: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.ui_doc_snapshot_boxes_type_0 import UiDocSnapshotBoxesType0

        data = self.data

        format_ = self.format_

        height = self.height

        mime = self.mime

        width = self.width

        boxes: dict[str, Any] | None | Unset
        if isinstance(self.boxes, Unset):
            boxes = UNSET
        elif isinstance(self.boxes, UiDocSnapshotBoxesType0):
            boxes = self.boxes.to_dict()
        else:
            boxes = self.boxes

        cached = self.cached

        node: None | str | Unset
        if isinstance(self.node, Unset):
            node = UNSET
        else:
            node = self.node

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "data": data,
                "format": format_,
                "height": height,
                "mime": mime,
                "width": width,
            }
        )
        if boxes is not UNSET:
            field_dict["boxes"] = boxes
        if cached is not UNSET:
            field_dict["cached"] = cached
        if node is not UNSET:
            field_dict["node"] = node

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_doc_snapshot_boxes_type_0 import UiDocSnapshotBoxesType0

        d = dict(src_dict)
        data = d.pop("data")

        format_ = d.pop("format")

        height = d.pop("height")

        mime = d.pop("mime")

        width = d.pop("width")

        def _parse_boxes(data: object) -> None | UiDocSnapshotBoxesType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                boxes_type_0 = UiDocSnapshotBoxesType0.from_dict(data)

                return boxes_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UiDocSnapshotBoxesType0 | Unset, data)

        boxes = _parse_boxes(d.pop("boxes", UNSET))

        cached = d.pop("cached", UNSET)

        def _parse_node(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        node = _parse_node(d.pop("node", UNSET))

        ui_doc_snapshot = cls(
            data=data,
            format_=format_,
            height=height,
            mime=mime,
            width=width,
            boxes=boxes,
            cached=cached,
            node=node,
        )

        ui_doc_snapshot.additional_properties = d
        return ui_doc_snapshot

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
