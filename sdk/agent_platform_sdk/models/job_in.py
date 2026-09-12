from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="JobIn")


@_attrs_define
class JobIn:
    """
    Attributes:
        cron (str):
        name (str):
        prompt (str):
        agent (None | str | Unset):
        relay_channel (None | str | Unset):
        timezone (str | Unset):  Default: ''.
    """

    cron: str
    name: str
    prompt: str
    agent: None | str | Unset = UNSET
    relay_channel: None | str | Unset = UNSET
    timezone: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        cron = self.cron

        name = self.name

        prompt = self.prompt

        agent: None | str | Unset
        if isinstance(self.agent, Unset):
            agent = UNSET
        else:
            agent = self.agent

        relay_channel: None | str | Unset
        if isinstance(self.relay_channel, Unset):
            relay_channel = UNSET
        else:
            relay_channel = self.relay_channel

        timezone = self.timezone

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "cron": cron,
                "name": name,
                "prompt": prompt,
            }
        )
        if agent is not UNSET:
            field_dict["agent"] = agent
        if relay_channel is not UNSET:
            field_dict["relay_channel"] = relay_channel
        if timezone is not UNSET:
            field_dict["timezone"] = timezone

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        cron = d.pop("cron")

        name = d.pop("name")

        prompt = d.pop("prompt")

        def _parse_agent(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        agent = _parse_agent(d.pop("agent", UNSET))

        def _parse_relay_channel(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        relay_channel = _parse_relay_channel(d.pop("relay_channel", UNSET))

        timezone = d.pop("timezone", UNSET)

        job_in = cls(
            cron=cron,
            name=name,
            prompt=prompt,
            agent=agent,
            relay_channel=relay_channel,
            timezone=timezone,
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
