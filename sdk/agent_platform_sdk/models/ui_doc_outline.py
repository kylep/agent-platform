from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="UiDocOutline")


@_attrs_define
class UiDocOutline:
    """
    Attributes:
        layout (str):
        slug (str):
        text (str):
        version (int):
        next_after (None | str | Unset):
    """

    layout: str
    slug: str
    text: str
    version: int
    next_after: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        layout = self.layout

        slug = self.slug

        text = self.text

        version = self.version

        next_after: None | str | Unset
        if isinstance(self.next_after, Unset):
            next_after = UNSET
        else:
            next_after = self.next_after

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "layout": layout,
                "slug": slug,
                "text": text,
                "version": version,
            }
        )
        if next_after is not UNSET:
            field_dict["next_after"] = next_after

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        layout = d.pop("layout")

        slug = d.pop("slug")

        text = d.pop("text")

        version = d.pop("version")

        def _parse_next_after(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        next_after = _parse_next_after(d.pop("next_after", UNSET))

        ui_doc_outline = cls(
            layout=layout,
            slug=slug,
            text=text,
            version=version,
            next_after=next_after,
        )

        ui_doc_outline.additional_properties = d
        return ui_doc_outline

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
