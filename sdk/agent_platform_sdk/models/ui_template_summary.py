from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="UiTemplateSummary")


@_attrs_define
class UiTemplateSummary:
    """
    Attributes:
        description (str):
        height (int):
        id (str):
        preview_node (str):
        revision (int):
        tags (list[str]):
        title (str):
        width (int):
    """

    description: str
    height: int
    id: str
    preview_node: str
    revision: int
    tags: list[str]
    title: str
    width: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        description = self.description

        height = self.height

        id = self.id

        preview_node = self.preview_node

        revision = self.revision

        tags = self.tags

        title = self.title

        width = self.width

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "description": description,
                "height": height,
                "id": id,
                "preview_node": preview_node,
                "revision": revision,
                "tags": tags,
                "title": title,
                "width": width,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        description = d.pop("description")

        height = d.pop("height")

        id = d.pop("id")

        preview_node = d.pop("preview_node")

        revision = d.pop("revision")

        tags = cast(list[str], d.pop("tags"))

        title = d.pop("title")

        width = d.pop("width")

        ui_template_summary = cls(
            description=description,
            height=height,
            id=id,
            preview_node=preview_node,
            revision=revision,
            tags=tags,
            title=title,
            width=width,
        )

        ui_template_summary.additional_properties = d
        return ui_template_summary

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
