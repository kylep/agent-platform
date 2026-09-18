from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="ArtifactStats")


@_attrs_define
class ArtifactStats:
    """
    Attributes:
        bytes_ (int):
        count (int):
        daily_cap_usd (float):
        generated_this_month (int):
        spend_this_month_usd (float):
        spend_today_usd (float):
        total_cap (int):
    """

    bytes_: int
    count: int
    daily_cap_usd: float
    generated_this_month: int
    spend_this_month_usd: float
    spend_today_usd: float
    total_cap: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        bytes_ = self.bytes_

        count = self.count

        daily_cap_usd = self.daily_cap_usd

        generated_this_month = self.generated_this_month

        spend_this_month_usd = self.spend_this_month_usd

        spend_today_usd = self.spend_today_usd

        total_cap = self.total_cap

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "bytes": bytes_,
                "count": count,
                "daily_cap_usd": daily_cap_usd,
                "generated_this_month": generated_this_month,
                "spend_this_month_usd": spend_this_month_usd,
                "spend_today_usd": spend_today_usd,
                "total_cap": total_cap,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        bytes_ = d.pop("bytes")

        count = d.pop("count")

        daily_cap_usd = d.pop("daily_cap_usd")

        generated_this_month = d.pop("generated_this_month")

        spend_this_month_usd = d.pop("spend_this_month_usd")

        spend_today_usd = d.pop("spend_today_usd")

        total_cap = d.pop("total_cap")

        artifact_stats = cls(
            bytes_=bytes_,
            count=count,
            daily_cap_usd=daily_cap_usd,
            generated_this_month=generated_this_month,
            spend_this_month_usd=spend_this_month_usd,
            spend_today_usd=spend_today_usd,
            total_cap=total_cap,
        )

        artifact_stats.additional_properties = d
        return artifact_stats

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
