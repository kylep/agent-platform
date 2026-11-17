from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.ui_font_info import UiFontInfo


T = TypeVar("T", bound="UiFontsOut")


@_attrs_define
class UiFontsOut:
    """
    Attributes:
        fonts (list[UiFontInfo]):
        slug (str):
        version (int):
    """

    fonts: list[UiFontInfo]
    slug: str
    version: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        fonts = []
        for fonts_item_data in self.fonts:
            fonts_item = fonts_item_data.to_dict()
            fonts.append(fonts_item)

        slug = self.slug

        version = self.version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "fonts": fonts,
                "slug": slug,
                "version": version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_font_info import UiFontInfo

        d = dict(src_dict)
        fonts = []
        _fonts = d.pop("fonts")
        for fonts_item_data in _fonts:
            fonts_item = UiFontInfo.from_dict(fonts_item_data)

            fonts.append(fonts_item)

        slug = d.pop("slug")

        version = d.pop("version")

        ui_fonts_out = cls(
            fonts=fonts,
            slug=slug,
            version=version,
        )

        ui_fonts_out.additional_properties = d
        return ui_fonts_out

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
