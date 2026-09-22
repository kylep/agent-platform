from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="Connector")


@_attrs_define
class Connector:
    """
    Attributes:
        description (str):
        implemented (bool):
        kind (str):
        name (str):
        secrets (list[str]):
    """

    description: str
    implemented: bool
    kind: str
    name: str
    secrets: list[str]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        description = self.description

        implemented = self.implemented

        kind = self.kind

        name = self.name

        secrets = self.secrets

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "description": description,
                "implemented": implemented,
                "kind": kind,
                "name": name,
                "secrets": secrets,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        description = d.pop("description")

        implemented = d.pop("implemented")

        kind = d.pop("kind")

        name = d.pop("name")

        secrets = cast(list[str], d.pop("secrets"))

        connector = cls(
            description=description,
            implemented=implemented,
            kind=kind,
            name=name,
            secrets=secrets,
        )

        connector.additional_properties = d
        return connector

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
