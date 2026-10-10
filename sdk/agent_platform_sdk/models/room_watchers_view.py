from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="RoomWatchersView")


@_attrs_define
class RoomWatchersView:
    """
    Attributes:
        agents (list[str]):
        channel_id (str):
        dispatch_mode (str):
        conversation (bool | Unset):  Default: False.
        turn_cap (int | Unset):  Default: 500.
        turns_per_hour (int | Unset):  Default: 12.
        warnings (list[str] | Unset):
    """

    agents: list[str]
    channel_id: str
    dispatch_mode: str
    conversation: bool | Unset = False
    turn_cap: int | Unset = 500
    turns_per_hour: int | Unset = 12
    warnings: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agents = self.agents

        channel_id = self.channel_id

        dispatch_mode = self.dispatch_mode

        conversation = self.conversation

        turn_cap = self.turn_cap

        turns_per_hour = self.turns_per_hour

        warnings: list[str] | Unset = UNSET
        if not isinstance(self.warnings, Unset):
            warnings = self.warnings

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agents": agents,
                "channel_id": channel_id,
                "dispatch_mode": dispatch_mode,
            }
        )
        if conversation is not UNSET:
            field_dict["conversation"] = conversation
        if turn_cap is not UNSET:
            field_dict["turn_cap"] = turn_cap
        if turns_per_hour is not UNSET:
            field_dict["turns_per_hour"] = turns_per_hour
        if warnings is not UNSET:
            field_dict["warnings"] = warnings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        agents = cast(list[str], d.pop("agents"))

        channel_id = d.pop("channel_id")

        dispatch_mode = d.pop("dispatch_mode")

        conversation = d.pop("conversation", UNSET)

        turn_cap = d.pop("turn_cap", UNSET)

        turns_per_hour = d.pop("turns_per_hour", UNSET)

        warnings = cast(list[str], d.pop("warnings", UNSET))

        room_watchers_view = cls(
            agents=agents,
            channel_id=channel_id,
            dispatch_mode=dispatch_mode,
            conversation=conversation,
            turn_cap=turn_cap,
            turns_per_hour=turns_per_hour,
            warnings=warnings,
        )

        room_watchers_view.additional_properties = d
        return room_watchers_view

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
