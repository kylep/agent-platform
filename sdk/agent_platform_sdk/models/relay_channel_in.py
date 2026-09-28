from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="RelayChannelIn")


@_attrs_define
class RelayChannelIn:
    """
    Attributes:
        kind (str | Unset):  Default: 'channel'.
        name (None | str | Unset):
        open_ (bool | None | Unset):
        participants (list[str] | Unset):
        topic (str | Unset):  Default: ''.
    """

    kind: str | Unset = "channel"
    name: None | str | Unset = UNSET
    open_: bool | None | Unset = UNSET
    participants: list[str] | Unset = UNSET
    topic: str | Unset = ""

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        open_: bool | None | Unset
        if isinstance(self.open_, Unset):
            open_ = UNSET
        else:
            open_ = self.open_

        participants: list[str] | Unset = UNSET
        if not isinstance(self.participants, Unset):
            participants = self.participants

        topic = self.topic

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if kind is not UNSET:
            field_dict["kind"] = kind
        if name is not UNSET:
            field_dict["name"] = name
        if open_ is not UNSET:
            field_dict["open"] = open_
        if participants is not UNSET:
            field_dict["participants"] = participants
        if topic is not UNSET:
            field_dict["topic"] = topic

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = d.pop("kind", UNSET)

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_open_(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        open_ = _parse_open_(d.pop("open", UNSET))

        participants = cast(list[str], d.pop("participants", UNSET))

        topic = d.pop("topic", UNSET)

        relay_channel_in = cls(
            kind=kind,
            name=name,
            open_=open_,
            participants=participants,
            topic=topic,
        )

        return relay_channel_in
