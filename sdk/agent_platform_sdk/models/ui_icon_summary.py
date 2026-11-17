from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="UiIconSummary")


@_attrs_define
class UiIconSummary:
    """
    Attributes:
        categories (list[str]):
        id (str):
        name (str):
        tags (list[str]):
    """

    categories: list[str]
    id: str
    name: str
    tags: list[str]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        categories = self.categories

        id = self.id

        name = self.name

        tags = self.tags

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "categories": categories,
                "id": id,
                "name": name,
                "tags": tags,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        categories = cast(list[str], d.pop("categories"))

        id = d.pop("id")

        name = d.pop("name")

        tags = cast(list[str], d.pop("tags"))

        ui_icon_summary = cls(
            categories=categories,
            id=id,
            name=name,
            tags=tags,
        )

        ui_icon_summary.additional_properties = d
        return ui_icon_summary

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
