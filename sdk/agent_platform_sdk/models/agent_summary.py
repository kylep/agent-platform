from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="AgentSummary")


@_attrs_define
class AgentSummary:
    """
    Attributes:
        blocked (bool):
        blocked_reason (None | str):
        description (str):
        error (None | str):
        name (str):
        quarantined (bool):
        schedule (str):
        system (bool):
    """

    blocked: bool
    blocked_reason: None | str
    description: str
    error: None | str
    name: str
    quarantined: bool
    schedule: str
    system: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        blocked = self.blocked

        blocked_reason: None | str
        blocked_reason = self.blocked_reason

        description = self.description

        error: None | str
        error = self.error

        name = self.name

        quarantined = self.quarantined

        schedule = self.schedule

        system = self.system

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "blocked": blocked,
                "blocked_reason": blocked_reason,
                "description": description,
                "error": error,
                "name": name,
                "quarantined": quarantined,
                "schedule": schedule,
                "system": system,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        blocked = d.pop("blocked")

        def _parse_blocked_reason(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        blocked_reason = _parse_blocked_reason(d.pop("blocked_reason"))

        description = d.pop("description")

        def _parse_error(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        error = _parse_error(d.pop("error"))

        name = d.pop("name")

        quarantined = d.pop("quarantined")

        schedule = d.pop("schedule")

        system = d.pop("system")

        agent_summary = cls(
            blocked=blocked,
            blocked_reason=blocked_reason,
            description=description,
            error=error,
            name=name,
            quarantined=quarantined,
            schedule=schedule,
            system=system,
        )

        agent_summary.additional_properties = d
        return agent_summary

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
