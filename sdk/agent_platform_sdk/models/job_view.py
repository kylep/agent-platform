from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="JobView")


@_attrs_define
class JobView:
    """
    Attributes:
        agent (str):
        cron (str):
        enabled (bool):
        id (str):
        last_fire (None | str):
        name (str):
        next_fire (None | str):
        prompt (str):
    """

    agent: str
    cron: str
    enabled: bool
    id: str
    last_fire: None | str
    name: str
    next_fire: None | str
    prompt: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent

        cron = self.cron

        enabled = self.enabled

        id = self.id

        last_fire: None | str
        last_fire = self.last_fire

        name = self.name

        next_fire: None | str
        next_fire = self.next_fire

        prompt = self.prompt

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "cron": cron,
                "enabled": enabled,
                "id": id,
                "last_fire": last_fire,
                "name": name,
                "next_fire": next_fire,
                "prompt": prompt,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        agent = d.pop("agent")

        cron = d.pop("cron")

        enabled = d.pop("enabled")

        id = d.pop("id")

        def _parse_last_fire(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        last_fire = _parse_last_fire(d.pop("last_fire"))

        name = d.pop("name")

        def _parse_next_fire(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        next_fire = _parse_next_fire(d.pop("next_fire"))

        prompt = d.pop("prompt")

        job_view = cls(
            agent=agent,
            cron=cron,
            enabled=enabled,
            id=id,
            last_fire=last_fire,
            name=name,
            next_fire=next_fire,
            prompt=prompt,
        )

        job_view.additional_properties = d
        return job_view

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
