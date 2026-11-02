from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="DeleteIn")


@_attrs_define
class DeleteIn:
    """
    Attributes:
        expected_revision (str):
        key (None | str | Unset):
        memory_id (None | str | Unset):
    """

    expected_revision: str
    key: None | str | Unset = UNSET
    memory_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        expected_revision = self.expected_revision

        key: None | str | Unset
        if isinstance(self.key, Unset):
            key = UNSET
        else:
            key = self.key

        memory_id: None | str | Unset
        if isinstance(self.memory_id, Unset):
            memory_id = UNSET
        else:
            memory_id = self.memory_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "expected_revision": expected_revision,
            }
        )
        if key is not UNSET:
            field_dict["key"] = key
        if memory_id is not UNSET:
            field_dict["memory_id"] = memory_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        expected_revision = d.pop("expected_revision")

        def _parse_key(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        key = _parse_key(d.pop("key", UNSET))

        def _parse_memory_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        memory_id = _parse_memory_id(d.pop("memory_id", UNSET))

        delete_in = cls(
            expected_revision=expected_revision,
            key=key,
            memory_id=memory_id,
        )

        delete_in.additional_properties = d
        return delete_in

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
