from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="ToolView")


@_attrs_define
class ToolView:
    """
    Attributes:
        database (bool):
        description (str):
        error (None | str):
        has_requirements (bool):
        name (str):
        secrets (list[str]):
        timeout_seconds (int):
        used_by (list[str]):
    """

    database: bool
    description: str
    error: None | str
    has_requirements: bool
    name: str
    secrets: list[str]
    timeout_seconds: int
    used_by: list[str]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        database = self.database

        description = self.description

        error: None | str
        error = self.error

        has_requirements = self.has_requirements

        name = self.name

        secrets = self.secrets

        timeout_seconds = self.timeout_seconds

        used_by = self.used_by

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "database": database,
                "description": description,
                "error": error,
                "has_requirements": has_requirements,
                "name": name,
                "secrets": secrets,
                "timeout_seconds": timeout_seconds,
                "used_by": used_by,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        database = d.pop("database")

        description = d.pop("description")

        def _parse_error(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        error = _parse_error(d.pop("error"))

        has_requirements = d.pop("has_requirements")

        name = d.pop("name")

        secrets = cast(list[str], d.pop("secrets"))

        timeout_seconds = d.pop("timeout_seconds")

        used_by = cast(list[str], d.pop("used_by"))

        tool_view = cls(
            database=database,
            description=description,
            error=error,
            has_requirements=has_requirements,
            name=name,
            secrets=secrets,
            timeout_seconds=timeout_seconds,
            used_by=used_by,
        )

        tool_view.additional_properties = d
        return tool_view

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
