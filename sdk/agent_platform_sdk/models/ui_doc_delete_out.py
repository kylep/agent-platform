from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="UiDocDeleteOut")


@_attrs_define
class UiDocDeleteOut:
    """
    Attributes:
        assets_removed (int | Unset):  Default: 0.
        bytes_freed (int | Unset):  Default: 0.
        hard (bool | Unset):  Default: False.
        ok (bool | Unset):  Default: True.
    """

    assets_removed: int | Unset = 0
    bytes_freed: int | Unset = 0
    hard: bool | Unset = False
    ok: bool | Unset = True
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        assets_removed = self.assets_removed

        bytes_freed = self.bytes_freed

        hard = self.hard

        ok = self.ok

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if assets_removed is not UNSET:
            field_dict["assets_removed"] = assets_removed
        if bytes_freed is not UNSET:
            field_dict["bytes_freed"] = bytes_freed
        if hard is not UNSET:
            field_dict["hard"] = hard
        if ok is not UNSET:
            field_dict["ok"] = ok

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        assets_removed = d.pop("assets_removed", UNSET)

        bytes_freed = d.pop("bytes_freed", UNSET)

        hard = d.pop("hard", UNSET)

        ok = d.pop("ok", UNSET)

        ui_doc_delete_out = cls(
            assets_removed=assets_removed,
            bytes_freed=bytes_freed,
            hard=hard,
            ok=ok,
        )

        ui_doc_delete_out.additional_properties = d
        return ui_doc_delete_out

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
