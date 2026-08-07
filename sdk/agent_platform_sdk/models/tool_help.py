from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="ToolHelp")


@_attrs_define
class ToolHelp:
    """
    Attributes:
        description (str):
        kind (str):
        name (str):
        sensitive (bool):
    """

    description: str
    kind: str
    name: str
    sensitive: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        description = self.description

        kind = self.kind

        name = self.name

        sensitive = self.sensitive

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "description": description,
                "kind": kind,
                "name": name,
                "sensitive": sensitive,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        description = d.pop("description")

        kind = d.pop("kind")

        name = d.pop("name")

        sensitive = d.pop("sensitive")

        tool_help = cls(
            description=description,
            kind=kind,
            name=name,
            sensitive=sensitive,
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
