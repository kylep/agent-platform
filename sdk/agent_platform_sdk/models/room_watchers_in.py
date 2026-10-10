from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="RoomWatchersIn")


@_attrs_define
class RoomWatchersIn:
    """The room's complete, ordered watcher list (docs/design/41). Empty
    restores mention-only dispatch.

        Attributes:
            agents (list[str] | Unset):
            conversation (bool | None | Unset):
            turn_cap (int | None | Unset):
            turns_per_hour (int | None | Unset):
    """

    agents: list[str] | Unset = UNSET
    conversation: bool | None | Unset = UNSET
    turn_cap: int | None | Unset = UNSET
    turns_per_hour: int | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        agents: list[str] | Unset = UNSET
        if not isinstance(self.agents, Unset):
            agents = self.agents

        conversation: bool | None | Unset
        if isinstance(self.conversation, Unset):
            conversation = UNSET
        else:
            conversation = self.conversation

        turn_cap: int | None | Unset
        if isinstance(self.turn_cap, Unset):
            turn_cap = UNSET
        else:
            turn_cap = self.turn_cap

        turns_per_hour: int | None | Unset
        if isinstance(self.turns_per_hour, Unset):
            turns_per_hour = UNSET
        else:
            turns_per_hour = self.turns_per_hour

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if agents is not UNSET:
            field_dict["agents"] = agents
        if conversation is not UNSET:
            field_dict["conversation"] = conversation
        if turn_cap is not UNSET:
            field_dict["turn_cap"] = turn_cap
        if turns_per_hour is not UNSET:
            field_dict["turns_per_hour"] = turns_per_hour

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        agents = cast(list[str], d.pop("agents", UNSET))

        def _parse_conversation(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        conversation = _parse_conversation(d.pop("conversation", UNSET))

        def _parse_turn_cap(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        turn_cap = _parse_turn_cap(d.pop("turn_cap", UNSET))

        def _parse_turns_per_hour(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        turns_per_hour = _parse_turns_per_hour(d.pop("turns_per_hour", UNSET))

        room_watchers_in = cls(
            agents=agents,
            conversation=conversation,
            turn_cap=turn_cap,
            turns_per_hour=turns_per_hour,
        )

        return room_watchers_in
