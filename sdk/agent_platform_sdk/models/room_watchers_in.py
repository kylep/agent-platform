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
    """

    agents: list[str] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        agents: list[str] | Unset = UNSET
        if not isinstance(self.agents, Unset):
            agents = self.agents

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if agents is not UNSET:
            field_dict["agents"] = agents

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        agents = cast(list[str], d.pop("agents", UNSET))

        room_watchers_in = cls(
            agents=agents,
        )

        return room_watchers_in
