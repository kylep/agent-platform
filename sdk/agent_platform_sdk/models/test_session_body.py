from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="TestSessionBody")


@_attrs_define
class TestSessionBody:
    """
    Attributes:
        length_min (int | Unset):  Default: 8.
        override (bool | Unset):  Default: True.
    """

    length_min: int | Unset = 8
    override: bool | Unset = True
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        length_min = self.length_min

        override = self.override

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if length_min is not UNSET:
            field_dict["length_min"] = length_min
        if override is not UNSET:
            field_dict["override"] = override

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        length_min = d.pop("length_min", UNSET)

        override = d.pop("override", UNSET)

        test_session_body = cls(
            length_min=length_min,
            override=override,
        )

        test_session_body.additional_properties = d
        return test_session_body

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
