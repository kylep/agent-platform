from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="BackupStatus")


@_attrs_define
class BackupStatus:
    """
    Attributes:
        shrunk (bool):
        stale (bool):
        stale_after_hours (int):
        failed_leftovers (list[str] | Unset):
        local_dump_age_hours (float | None | Unset):
        newest (None | str | Unset):
        newest_age_hours (float | None | Unset):
        newest_bytes (int | None | Unset):
        previous_bytes (int | None | Unset):
        uploaded_count (int | Unset):  Default: 0.
    """

    shrunk: bool
    stale: bool
    stale_after_hours: int
    failed_leftovers: list[str] | Unset = UNSET
    local_dump_age_hours: float | None | Unset = UNSET
    newest: None | str | Unset = UNSET
    newest_age_hours: float | None | Unset = UNSET
    newest_bytes: int | None | Unset = UNSET
    previous_bytes: int | None | Unset = UNSET
    uploaded_count: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        shrunk = self.shrunk

        stale = self.stale

        stale_after_hours = self.stale_after_hours

        failed_leftovers: list[str] | Unset = UNSET
        if not isinstance(self.failed_leftovers, Unset):
            failed_leftovers = self.failed_leftovers

        local_dump_age_hours: float | None | Unset
        if isinstance(self.local_dump_age_hours, Unset):
            local_dump_age_hours = UNSET
        else:
            local_dump_age_hours = self.local_dump_age_hours

        newest: None | str | Unset
        if isinstance(self.newest, Unset):
            newest = UNSET
        else:
            newest = self.newest

        newest_age_hours: float | None | Unset
        if isinstance(self.newest_age_hours, Unset):
            newest_age_hours = UNSET
        else:
            newest_age_hours = self.newest_age_hours

        newest_bytes: int | None | Unset
        if isinstance(self.newest_bytes, Unset):
            newest_bytes = UNSET
        else:
            newest_bytes = self.newest_bytes

        previous_bytes: int | None | Unset
        if isinstance(self.previous_bytes, Unset):
            previous_bytes = UNSET
        else:
            previous_bytes = self.previous_bytes

        uploaded_count = self.uploaded_count

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "shrunk": shrunk,
                "stale": stale,
                "stale_after_hours": stale_after_hours,
            }
        )
        if failed_leftovers is not UNSET:
            field_dict["failed_leftovers"] = failed_leftovers
        if local_dump_age_hours is not UNSET:
            field_dict["local_dump_age_hours"] = local_dump_age_hours
        if newest is not UNSET:
            field_dict["newest"] = newest
        if newest_age_hours is not UNSET:
            field_dict["newest_age_hours"] = newest_age_hours
        if newest_bytes is not UNSET:
            field_dict["newest_bytes"] = newest_bytes
        if previous_bytes is not UNSET:
            field_dict["previous_bytes"] = previous_bytes
        if uploaded_count is not UNSET:
            field_dict["uploaded_count"] = uploaded_count

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        shrunk = d.pop("shrunk")

        stale = d.pop("stale")

        stale_after_hours = d.pop("stale_after_hours")

        failed_leftovers = cast(list[str], d.pop("failed_leftovers", UNSET))

        def _parse_local_dump_age_hours(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        local_dump_age_hours = _parse_local_dump_age_hours(
            d.pop("local_dump_age_hours", UNSET)
        )

        def _parse_newest(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        newest = _parse_newest(d.pop("newest", UNSET))

        def _parse_newest_age_hours(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        newest_age_hours = _parse_newest_age_hours(d.pop("newest_age_hours", UNSET))

        def _parse_newest_bytes(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        newest_bytes = _parse_newest_bytes(d.pop("newest_bytes", UNSET))

        def _parse_previous_bytes(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        previous_bytes = _parse_previous_bytes(d.pop("previous_bytes", UNSET))

        uploaded_count = d.pop("uploaded_count", UNSET)

        backup_status = cls(
            shrunk=shrunk,
            stale=stale,
            stale_after_hours=stale_after_hours,
            failed_leftovers=failed_leftovers,
            local_dump_age_hours=local_dump_age_hours,
            newest=newest,
            newest_age_hours=newest_age_hours,
            newest_bytes=newest_bytes,
            previous_bytes=previous_bytes,
            uploaded_count=uploaded_count,
        )

        backup_status.additional_properties = d
        return backup_status

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
