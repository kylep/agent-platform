from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="SkillView")


@_attrs_define
class SkillView:
    """
    Attributes:
        description (str):
        error (None | str):
        icon (str):
        name (str):
        secrets (list[str]):
        used_by (list[str]):
    """

    description: str
    error: None | str
    icon: str
    name: str
    secrets: list[str]
    used_by: list[str]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        description = self.description

        error: None | str
        error = self.error

        icon = self.icon

        name = self.name

        secrets = self.secrets

        used_by = self.used_by

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "description": description,
                "error": error,
                "icon": icon,
                "name": name,
                "secrets": secrets,
                "used_by": used_by,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        description = d.pop("description")

        def _parse_error(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        error = _parse_error(d.pop("error"))

        icon = d.pop("icon")

        name = d.pop("name")

        secrets = cast(list[str], d.pop("secrets"))

        used_by = cast(list[str], d.pop("used_by"))

        skill_view = cls(
            description=description,
            error=error,
            icon=icon,
            name=name,
            secrets=secrets,
            used_by=used_by,
        )

        skill_view.additional_properties = d
        return skill_view

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
