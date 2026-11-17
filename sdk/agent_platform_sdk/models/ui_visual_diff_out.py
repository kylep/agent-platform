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
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ui_visual_diff_region import UiVisualDiffRegion


T = TypeVar("T", bound="UiVisualDiffOut")


@_attrs_define
class UiVisualDiffOut:
    """
    Attributes:
        base_image (str):
        box (list[float]):
        changed_pixels (int):
        from_version (int):
        height (int):
        image (str):
        mime (Literal['image/png']):
        node_ids (list[str]):
        regions (list[UiVisualDiffRegion]):
        slug (str):
        to_version (int):
        total_pixels (int):
        truncated (bool):
        width (int):
        warnings (list[str] | Unset):
    """

    base_image: str
    box: list[float]
    changed_pixels: int
    from_version: int
    height: int
    image: str
    mime: Literal["image/png"]
    node_ids: list[str]
    regions: list[UiVisualDiffRegion]
    slug: str
    to_version: int
    total_pixels: int
    truncated: bool
    width: int
    warnings: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        base_image = self.base_image

        box = self.box

        changed_pixels = self.changed_pixels

        from_version = self.from_version

        height = self.height

        image = self.image

        mime = self.mime

        node_ids = self.node_ids

        regions = []
        for regions_item_data in self.regions:
            regions_item = regions_item_data.to_dict()
            regions.append(regions_item)

        slug = self.slug

        to_version = self.to_version

        total_pixels = self.total_pixels

        truncated = self.truncated

        width = self.width

        warnings: list[str] | Unset = UNSET
        if not isinstance(self.warnings, Unset):
            warnings = self.warnings

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "base_image": base_image,
                "box": box,
                "changed_pixels": changed_pixels,
                "from_version": from_version,
                "height": height,
                "image": image,
                "mime": mime,
                "node_ids": node_ids,
                "regions": regions,
                "slug": slug,
                "to_version": to_version,
                "total_pixels": total_pixels,
                "truncated": truncated,
                "width": width,
            }
        )
        if warnings is not UNSET:
            field_dict["warnings"] = warnings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_visual_diff_region import UiVisualDiffRegion

        d = dict(src_dict)
        base_image = d.pop("base_image")

        box = cast(list[float], d.pop("box"))

        changed_pixels = d.pop("changed_pixels")

        from_version = d.pop("from_version")

        height = d.pop("height")

        image = d.pop("image")

        mime = cast(Literal["image/png"], d.pop("mime"))
        if mime != "image/png":
            raise ValueError(f"mime must match const 'image/png', got '{mime}'")

        node_ids = cast(list[str], d.pop("node_ids"))

        regions = []
        _regions = d.pop("regions")
        for regions_item_data in _regions:
            regions_item = UiVisualDiffRegion.from_dict(regions_item_data)

            regions.append(regions_item)

        slug = d.pop("slug")

        to_version = d.pop("to_version")

        total_pixels = d.pop("total_pixels")

        truncated = d.pop("truncated")

        width = d.pop("width")

        warnings = cast(list[str], d.pop("warnings", UNSET))

        ui_visual_diff_out = cls(
            base_image=base_image,
            box=box,
            changed_pixels=changed_pixels,
            from_version=from_version,
            height=height,
            image=image,
            mime=mime,
            node_ids=node_ids,
            regions=regions,
            slug=slug,
            to_version=to_version,
            total_pixels=total_pixels,
            truncated=truncated,
            width=width,
            warnings=warnings,
        )

        ui_visual_diff_out.additional_properties = d
        return ui_visual_diff_out

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
