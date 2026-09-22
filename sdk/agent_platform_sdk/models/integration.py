from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="Integration")


@_attrs_define
class Integration:
    """
    Attributes:
        configured (bool):
        detail (str):
        kind (str):
        name (str):
        secrets (list[str]):
        status (str):
    """

    configured: bool
    detail: str
    kind: str
    name: str
    secrets: list[str]
    status: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        configured = self.configured

        detail = self.detail

        kind = self.kind

        name = self.name

        secrets = self.secrets

        status = self.status

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "configured": configured,
                "detail": detail,
                "kind": kind,
                "name": name,
                "secrets": secrets,
                "status": status,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        configured = d.pop("configured")

        detail = d.pop("detail")

        kind = d.pop("kind")

        name = d.pop("name")

        secrets = cast(list[str], d.pop("secrets"))

        status = d.pop("status")

        integration = cls(
            configured=configured,
            detail=detail,
            kind=kind,
            name=name,
            secrets=secrets,
            status=status,
        )

        integration.additional_properties = d
        return integration

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
