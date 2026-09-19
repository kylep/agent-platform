from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="QuotaOk")


@_attrs_define
class QuotaOk:
    """The reading turned into a decision (docs/design/24): whether the caller
    may start expensive work now. `ok` is the field a model decides on; the
    percentages and the thresholds beside it are what it was decided from, so
    a "no" can be explained without re-reading the snapshot. The thresholds
    are the caller's own row when the caller is an agent and the column
    defaults when it is a person.

        Attributes:
            five_hour_max_pct (int):
            five_hour_pct (int | None):
            ok (bool):
            reason (str):
            seven_day_max_pct (int):
            seven_day_pct (int | None):
            stale (bool):
    """

    five_hour_max_pct: int
    five_hour_pct: int | None
    ok: bool
    reason: str
    seven_day_max_pct: int
    seven_day_pct: int | None
    stale: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        five_hour_max_pct = self.five_hour_max_pct

        five_hour_pct: int | None
        five_hour_pct = self.five_hour_pct

        ok = self.ok

        reason = self.reason

        seven_day_max_pct = self.seven_day_max_pct

        seven_day_pct: int | None
        seven_day_pct = self.seven_day_pct

        stale = self.stale

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "five_hour_max_pct": five_hour_max_pct,
                "five_hour_pct": five_hour_pct,
                "ok": ok,
                "reason": reason,
                "seven_day_max_pct": seven_day_max_pct,
                "seven_day_pct": seven_day_pct,
                "stale": stale,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        five_hour_max_pct = d.pop("five_hour_max_pct")

        def _parse_five_hour_pct(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        five_hour_pct = _parse_five_hour_pct(d.pop("five_hour_pct"))

        ok = d.pop("ok")

        reason = d.pop("reason")

        seven_day_max_pct = d.pop("seven_day_max_pct")

        def _parse_seven_day_pct(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        seven_day_pct = _parse_seven_day_pct(d.pop("seven_day_pct"))

        stale = d.pop("stale")

        quota_ok = cls(
            five_hour_max_pct=five_hour_max_pct,
            five_hour_pct=five_hour_pct,
            ok=ok,
            reason=reason,
            seven_day_max_pct=seven_day_max_pct,
            seven_day_pct=seven_day_pct,
            stale=stale,
        )

        quota_ok.additional_properties = d
        return quota_ok

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
