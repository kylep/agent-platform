from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="ToolHelp")


@_attrs_define
class ToolHelp:
    """
    Attributes:
        category (str):
        description (str):
        kind (str):
        name (str):
        sensitive (bool):
        dev_only (bool | Unset):  Default: False.
        display_name (None | str | Unset):
    """

    category: str
    description: str
    kind: str
    name: str
    sensitive: bool
    dev_only: bool | Unset = False
    display_name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        category = self.category

        description = self.description

        kind = self.kind

        name = self.name

        sensitive = self.sensitive

        dev_only = self.dev_only

        display_name: None | str | Unset
        if isinstance(self.display_name, Unset):
            display_name = UNSET
        else:
            display_name = self.display_name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "category": category,
                "description": description,
                "kind": kind,
                "name": name,
                "sensitive": sensitive,
            }
        )
        if dev_only is not UNSET:
            field_dict["dev_only"] = dev_only
        if display_name is not UNSET:
            field_dict["display_name"] = display_name

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        category = d.pop("category")

        description = d.pop("description")

        kind = d.pop("kind")

        name = d.pop("name")

        sensitive = d.pop("sensitive")

        dev_only = d.pop("dev_only", UNSET)

        def _parse_display_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        display_name = _parse_display_name(d.pop("display_name", UNSET))

        tool_help = cls(
            category=category,
            description=description,
            kind=kind,
            name=name,
            sensitive=sensitive,
            dev_only=dev_only,
            display_name=display_name,
        )

        tool_help.additional_properties = d
        return tool_help

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
