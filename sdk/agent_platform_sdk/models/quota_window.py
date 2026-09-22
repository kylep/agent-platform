from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="QuotaWindow")


@_attrs_define
class QuotaWindow:
    """One rate-limit window. `utilization` is a FRACTION whichever form the
    header arrived in (`quota.parse_utilization`), so a bar never has to guess
    whether 22 means a fifth or everything.

        Attributes:
            resets_at (None | str):
            utilization (float | None):
    """

    resets_at: None | str
    utilization: float | None
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        resets_at: None | str
        resets_at = self.resets_at

        utilization: float | None
        utilization = self.utilization

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "resets_at": resets_at,
                "utilization": utilization,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_resets_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        resets_at = _parse_resets_at(d.pop("resets_at"))

        def _parse_utilization(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        utilization = _parse_utilization(d.pop("utilization"))

        quota_window = cls(
            resets_at=resets_at,
            utilization=utilization,
        )

        quota_window.additional_properties = d
        return quota_window

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
