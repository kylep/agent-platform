from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="ToolWizardSecret")


@_attrs_define
class ToolWizardSecret:
    """
    Attributes:
        name (str):
        description (str | Unset):  Default: ''.
        env_var (str | Unset):  Default: ''.
    """

    name: str
    description: str | Unset = ""
    env_var: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        description = self.description

        env_var = self.env_var

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if env_var is not UNSET:
            field_dict["env_var"] = env_var

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        name = d.pop("name")

        description = d.pop("description", UNSET)

        env_var = d.pop("env_var", UNSET)

        tool_wizard_secret = cls(
            name=name,
            description=description,
            env_var=env_var,
        )

        tool_wizard_secret.additional_properties = d
        return tool_wizard_secret

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
