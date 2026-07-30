from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="JobIn")


@_attrs_define
class JobIn:
    """
    Attributes:
        agent (str):
        cron (str):
        name (str):
        prompt (str):
    """

    agent: str
    cron: str
    name: str
    prompt: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent

        cron = self.cron

        name = self.name

        prompt = self.prompt

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "cron": cron,
                "name": name,
                "prompt": prompt,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        agent = d.pop("agent")

        cron = d.pop("cron")

        name = d.pop("name")

        prompt = d.pop("prompt")

        job_in = cls(
            agent=agent,
            cron=cron,
            name=name,
            prompt=prompt,
        )

        job_in.additional_properties = d
        return job_in

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
