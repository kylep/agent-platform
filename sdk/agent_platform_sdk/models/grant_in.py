from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="GrantIn")


@_attrs_define
class GrantIn:
    """
    Attributes:
        app_name (str):
        operation (str):
        principal_id (str):
    """

    app_name: str
    operation: str
    principal_id: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        app_name = self.app_name

        operation = self.operation

        principal_id = self.principal_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "app_name": app_name,
                "operation": operation,
                "principal_id": principal_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        app_name = d.pop("app_name")

        operation = d.pop("operation")

        principal_id = d.pop("principal_id")

        grant_in = cls(
            app_name=app_name,
            operation=operation,
            principal_id=principal_id,
        )

        grant_in.additional_properties = d
        return grant_in

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
