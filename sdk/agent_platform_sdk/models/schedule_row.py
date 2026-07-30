from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="ScheduleRow")


@_attrs_define
class ScheduleRow:
    """
    Attributes:
        agent (str):
        cron (str):
        enabled (bool):
        last_fire (datetime.datetime | None):
        next_fire (datetime.datetime | None):
    """

    agent: str
    cron: str
    enabled: bool
    last_fire: datetime.datetime | None
    next_fire: datetime.datetime | None
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent

        cron = self.cron

        enabled = self.enabled

        last_fire: None | str
        if isinstance(self.last_fire, datetime.datetime):
            last_fire = self.last_fire.isoformat()
        else:
            last_fire = self.last_fire

        next_fire: None | str
        if isinstance(self.next_fire, datetime.datetime):
            next_fire = self.next_fire.isoformat()
        else:
            next_fire = self.next_fire

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "cron": cron,
                "enabled": enabled,
                "last_fire": last_fire,
                "next_fire": next_fire,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        agent = d.pop("agent")

        cron = d.pop("cron")

        enabled = d.pop("enabled")

        def _parse_last_fire(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_fire_type_0 = datetime.datetime.fromisoformat(data)

                return last_fire_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        last_fire = _parse_last_fire(d.pop("last_fire"))

        def _parse_next_fire(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                next_fire_type_0 = datetime.datetime.fromisoformat(data)

                return next_fire_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        next_fire = _parse_next_fire(d.pop("next_fire"))

        schedule_row = cls(
            agent=agent,
            cron=cron,
            enabled=enabled,
            last_fire=last_fire,
            next_fire=next_fire,
        )

        schedule_row.additional_properties = d
        return schedule_row

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
