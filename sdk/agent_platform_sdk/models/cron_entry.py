from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="CronEntry")


@_attrs_define
class CronEntry:
    """A durable cron trigger. Unlike the old entrypoints.yaml (bare
    expressions), each fire carries its own prompt — the same 1:many shape as
    ScheduledJob, so an agent can have two rhythms with different asks.

        Attributes:
            schedule (str):
            memory_query (None | str | Unset): Optional detached retrieval query: nonblank, at most 1024 UTF-8 bytes. Null
                uses the existing execution-prompt query; PATCH omission preserves the stored value. Creation omission defaults
                to null. Does not change instructions or access.
            model (str | Unset):  Default: ''.
            prompt (str | Unset):  Default: ''.
    """

    schedule: str
    memory_query: None | str | Unset = UNSET
    model: str | Unset = ""
    prompt: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        schedule = self.schedule

        memory_query: None | str | Unset
        if isinstance(self.memory_query, Unset):
            memory_query = UNSET
        else:
            memory_query = self.memory_query

        model = self.model

        prompt = self.prompt

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "schedule": schedule,
            }
        )
        if memory_query is not UNSET:
            field_dict["memory_query"] = memory_query
        if model is not UNSET:
            field_dict["model"] = model
        if prompt is not UNSET:
            field_dict["prompt"] = prompt

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        schedule = d.pop("schedule")

        def _parse_memory_query(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        memory_query = _parse_memory_query(d.pop("memory_query", UNSET))

        model = d.pop("model", UNSET)

        prompt = d.pop("prompt", UNSET)

        cron_entry = cls(
            schedule=schedule,
            memory_query=memory_query,
            model=model,
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
