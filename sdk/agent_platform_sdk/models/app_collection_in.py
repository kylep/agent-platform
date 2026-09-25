from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="AppCollectionIn")


@_attrs_define
class AppCollectionIn:
    """
    Attributes:
        display_name (str):
        name (str):
        description (str | Unset):  Default: ''.
        icon (str | Unset):  Default: ''.
    """

    display_name: str
    name: str
    description: str | Unset = ""
    icon: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        display_name = self.display_name

        name = self.name

        description = self.description

        icon = self.icon

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "display_name": display_name,
                "name": name,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if icon is not UNSET:
            field_dict["icon"] = icon

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        display_name = d.pop("display_name")

        name = d.pop("name")

        description = d.pop("description", UNSET)

        icon = d.pop("icon", UNSET)

        app_collection_in = cls(
            display_name=display_name,
            name=name,
            description=description,
            icon=icon,
        )

        app_collection_in.additional_properties = d
        return app_collection_in

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
