from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="UiDocPruneIn")


@_attrs_define
class UiDocPruneIn:
    """
    Attributes:
        older_than_days (int):
        keep_last (int | Unset):  Default: 20.
    """

    older_than_days: int
    keep_last: int | Unset = 20
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        older_than_days = self.older_than_days

        keep_last = self.keep_last

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "older_than_days": older_than_days,
            }
        )
        if keep_last is not UNSET:
            field_dict["keep_last"] = keep_last

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        older_than_days = d.pop("older_than_days")

        keep_last = d.pop("keep_last", UNSET)

        ui_doc_prune_in = cls(
            older_than_days=older_than_days,
            keep_last=keep_last,
        )

        ui_doc_prune_in.additional_properties = d
        return ui_doc_prune_in

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
