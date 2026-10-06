from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SelfCron")


@_attrs_define
class SelfCron:
    """A cron entry an agent writes for itself: a bounded prompt, and a model
    that is empty (the agent's own) or one the platform knows about.

        Attributes:
            schedule (str):
            model (str | Unset):  Default: ''.
            prompt (str | Unset):  Default: ''.
    """

    schedule: str
    model: str | Unset = ""
    prompt: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        schedule = self.schedule

        model = self.model

        prompt = self.prompt

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "schedule": schedule,
            }
        )
        if model is not UNSET:
            field_dict["model"] = model
        if prompt is not UNSET:
            field_dict["prompt"] = prompt

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        schedule = d.pop("schedule")

        model = d.pop("model", UNSET)

        prompt = d.pop("prompt", UNSET)

        self_cron = cls(
            schedule=schedule,
            model=model,
            prompt=prompt,
        )

        self_cron.additional_properties = d
        return self_cron

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
