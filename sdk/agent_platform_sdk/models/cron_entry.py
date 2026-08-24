from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="CronEntry")


@_attrs_define
class CronEntry:
    """A durable cron trigger. Unlike the old entrypoints.yaml (bare
    expressions), each fire carries its own prompt — the same 1:many shape as
    ScheduledJob, so an agent can have two rhythms with different asks.

        Attributes:
            schedule (str):
            prompt (str | Unset):  Default: ''.
    """

    schedule: str
    prompt: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        schedule = self.schedule

        prompt = self.prompt

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "schedule": schedule,
            }
        )
        if prompt is not UNSET:
            field_dict["prompt"] = prompt

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        schedule = d.pop("schedule")

        prompt = d.pop("prompt", UNSET)

        cron_entry = cls(
            schedule=schedule,
            prompt=prompt,
        )

        cron_entry.additional_properties = d
        return cron_entry

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
