from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="RelayBudget")


@_attrs_define
class RelayBudget:
    """
    Attributes:
        channel_per_hour (int):
        global_per_hour (int):
        global_used_last_hour (int):
    """

    channel_per_hour: int
    global_per_hour: int
    global_used_last_hour: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        channel_per_hour = self.channel_per_hour

        global_per_hour = self.global_per_hour

        global_used_last_hour = self.global_used_last_hour

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "channel_per_hour": channel_per_hour,
                "global_per_hour": global_per_hour,
                "global_used_last_hour": global_used_last_hour,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        channel_per_hour = d.pop("channel_per_hour")

        global_per_hour = d.pop("global_per_hour")

        global_used_last_hour = d.pop("global_used_last_hour")

        relay_budget = cls(
            channel_per_hour=channel_per_hour,
            global_per_hour=global_per_hour,
            global_used_last_hour=global_used_last_hour,
        )

        relay_budget.additional_properties = d
        return relay_budget

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
