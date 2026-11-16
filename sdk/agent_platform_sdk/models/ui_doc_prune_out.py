from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="UiDocPruneOut")


@_attrs_define
class UiDocPruneOut:
    """
    Attributes:
        assets_removed (int):
        bytes_freed (int):
        removed_rows (int):
    """

    assets_removed: int
    bytes_freed: int
    removed_rows: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        assets_removed = self.assets_removed

        bytes_freed = self.bytes_freed

        removed_rows = self.removed_rows

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "assets_removed": assets_removed,
                "bytes_freed": bytes_freed,
                "removed_rows": removed_rows,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        assets_removed = d.pop("assets_removed")

        bytes_freed = d.pop("bytes_freed")

        removed_rows = d.pop("removed_rows")

        ui_doc_prune_out = cls(
            assets_removed=assets_removed,
            bytes_freed=bytes_freed,
            removed_rows=removed_rows,
        )

        ui_doc_prune_out.additional_properties = d
        return ui_doc_prune_out

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
