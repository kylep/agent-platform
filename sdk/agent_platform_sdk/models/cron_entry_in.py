from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="CronEntryIn")


@_attrs_define
class CronEntryIn:
    """
    Attributes:
        schedule (str):
        prompt (str | Unset):  Default: ''.
    """

    schedule: str
    prompt: str | Unset = ""

    def to_dict(self) -> dict[str, Any]:
        schedule = self.schedule

        prompt = self.prompt

        field_dict: dict[str, Any] = {}

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

        cron_entry_in = cls(
            schedule=schedule,
            prompt=prompt,
        )

        return cron_entry_in
