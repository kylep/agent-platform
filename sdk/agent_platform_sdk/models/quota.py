from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.quota_window import QuotaWindow


T = TypeVar("T", bound="Quota")


@_attrs_define
class Quota:
    """The snapshot, as `quota_store.serialize` produces it — the one shape the
    REST body, the SSE frame and the `quota.events` payload share.

        Attributes:
            age_seconds (int | None):
            five_hour (QuotaWindow): One rate-limit window. `utilization` is a FRACTION whichever form the
                header arrived in (`quota.parse_utilization`), so a bar never has to guess
                whether 22 means a fifth or everything.
            observed_at (None | str):
            seven_day (QuotaWindow): One rate-limit window. `utilization` is a FRACTION whichever form the
                header arrived in (`quota.parse_utilization`), so a bar never has to guess
                whether 22 means a fifth or everything.
            source (None | str):
            stale (bool):
            status (None | str):
            probe (None | str | Unset):
    """

    age_seconds: int | None
    five_hour: QuotaWindow
    observed_at: None | str
    seven_day: QuotaWindow
    source: None | str
    stale: bool
    status: None | str
    probe: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        age_seconds: int | None
        age_seconds = self.age_seconds

        five_hour = self.five_hour.to_dict()

        observed_at: None | str
        observed_at = self.observed_at

        seven_day = self.seven_day.to_dict()

        source: None | str
        source = self.source

        stale = self.stale

        status: None | str
        status = self.status

        probe: None | str | Unset
        if isinstance(self.probe, Unset):
            probe = UNSET
        else:
            probe = self.probe

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "age_seconds": age_seconds,
                "five_hour": five_hour,
                "observed_at": observed_at,
                "seven_day": seven_day,
                "source": source,
                "stale": stale,
                "status": status,
            }
        )
        if probe is not UNSET:
            field_dict["probe"] = probe

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.quota_window import QuotaWindow

        d = dict(src_dict)

        def _parse_age_seconds(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        age_seconds = _parse_age_seconds(d.pop("age_seconds"))

        five_hour = QuotaWindow.from_dict(d.pop("five_hour"))

        def _parse_observed_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        observed_at = _parse_observed_at(d.pop("observed_at"))

        seven_day = QuotaWindow.from_dict(d.pop("seven_day"))

        def _parse_source(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        source = _parse_source(d.pop("source"))

        stale = d.pop("stale")

        def _parse_status(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        status = _parse_status(d.pop("status"))

        def _parse_probe(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        probe = _parse_probe(d.pop("probe", UNSET))

        quota = cls(
            age_seconds=age_seconds,
            five_hour=five_hour,
            observed_at=observed_at,
            seven_day=seven_day,
            source=source,
            stale=stale,
            status=status,
            probe=probe,
        )

        quota.additional_properties = d
        return quota

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
