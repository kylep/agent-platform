from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="CronEntryIn")


@_attrs_define
class CronEntryIn:
    """
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

        cron_entry_in = cls(
            schedule=schedule,
            memory_query=memory_query,
            model=model,
            prompt=prompt,
        )

        return cron_entry_in
