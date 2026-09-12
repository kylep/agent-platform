from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="JobRunAccepted")


@_attrs_define
class JobRunAccepted:
    """What Run Now created: a run id for an agent job, a MESSAGE id for a relay
    job. One field because the caller's next move is the same either way — show
    the thing it just started — and `relay_channel` says which kind it is.

        Attributes:
            agent (None | str):
            id (str):
            relay_channel (None | str | Unset):
    """

    agent: None | str
    id: str
    relay_channel: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent: None | str
        agent = self.agent

        id = self.id

        relay_channel: None | str | Unset
        if isinstance(self.relay_channel, Unset):
            relay_channel = UNSET
        else:
            relay_channel = self.relay_channel

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "id": id,
            }
        )
        if relay_channel is not UNSET:
            field_dict["relay_channel"] = relay_channel

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_agent(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        agent = _parse_agent(d.pop("agent"))

        id = d.pop("id")

        def _parse_relay_channel(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        relay_channel = _parse_relay_channel(d.pop("relay_channel", UNSET))

        job_run_accepted = cls(
            agent=agent,
            id=id,
            relay_channel=relay_channel,
        )

        job_run_accepted.additional_properties = d
        return job_run_accepted

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
