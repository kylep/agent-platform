from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="IncidentIn")


@_attrs_define
class IncidentIn:
    """
    Attributes:
        incident_key (str):
        body (str | Unset):  Default: ''.
        resolved (bool | Unset):  Default: False.
        title (str | Unset):  Default: ''.
    """

    incident_key: str
    body: str | Unset = ""
    resolved: bool | Unset = False
    title: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        incident_key = self.incident_key

        body = self.body

        resolved = self.resolved

        title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "incident_key": incident_key,
            }
        )
        if body is not UNSET:
            field_dict["body"] = body
        if resolved is not UNSET:
            field_dict["resolved"] = resolved
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        incident_key = d.pop("incident_key")

        body = d.pop("body", UNSET)

        resolved = d.pop("resolved", UNSET)

        title = d.pop("title", UNSET)

        incident_in = cls(
            incident_key=incident_key,
            body=body,
            resolved=resolved,
            title=title,
        )

        incident_in.additional_properties = d
        return incident_in

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
