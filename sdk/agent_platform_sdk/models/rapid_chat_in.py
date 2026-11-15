from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.rapid_chat_area_in import RapidChatAreaIn
    from ..models.rapid_chat_limits_in import RapidChatLimitsIn
    from ..models.rapid_chat_sink_in import RapidChatSinkIn


T = TypeVar("T", bound="RapidChatIn")


@_attrs_define
class RapidChatIn:
    """
    Attributes:
        goal (str):
        participants (list[str]):
        sink (RapidChatSinkIn):
        title (str):
        areas (list[RapidChatAreaIn | str] | Unset):
        duration_minutes (int | Unset):  Default: 60.
        limits (RapidChatLimitsIn | Unset):
        max_turns (int | Unset):  Default: 200.
        turn_timeout_seconds (int | Unset):  Default: 240.
    """

    goal: str
    participants: list[str]
    sink: RapidChatSinkIn
    title: str
    areas: list[RapidChatAreaIn | str] | Unset = UNSET
    duration_minutes: int | Unset = 60
    limits: RapidChatLimitsIn | Unset = UNSET
    max_turns: int | Unset = 200
    turn_timeout_seconds: int | Unset = 240

    def to_dict(self) -> dict[str, Any]:
        from ..models.rapid_chat_area_in import RapidChatAreaIn

        goal = self.goal

        participants = self.participants

        sink = self.sink.to_dict()

        title = self.title

        areas: list[dict[str, Any] | str] | Unset = UNSET
        if not isinstance(self.areas, Unset):
            areas = []
            for areas_item_data in self.areas:
                areas_item: dict[str, Any] | str
                if isinstance(areas_item_data, RapidChatAreaIn):
                    areas_item = areas_item_data.to_dict()
                else:
                    areas_item = areas_item_data
                areas.append(areas_item)

        duration_minutes = self.duration_minutes

        limits: dict[str, Any] | Unset = UNSET
        if not isinstance(self.limits, Unset):
            limits = self.limits.to_dict()

        max_turns = self.max_turns

        turn_timeout_seconds = self.turn_timeout_seconds

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "goal": goal,
                "participants": participants,
                "sink": sink,
                "title": title,
            }
        )
        if areas is not UNSET:
            field_dict["areas"] = areas
        if duration_minutes is not UNSET:
            field_dict["duration_minutes"] = duration_minutes
        if limits is not UNSET:
            field_dict["limits"] = limits
        if max_turns is not UNSET:
            field_dict["max_turns"] = max_turns
        if turn_timeout_seconds is not UNSET:
            field_dict["turn_timeout_seconds"] = turn_timeout_seconds

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.rapid_chat_area_in import RapidChatAreaIn
        from ..models.rapid_chat_limits_in import RapidChatLimitsIn
        from ..models.rapid_chat_sink_in import RapidChatSinkIn

        d = dict(src_dict)
        goal = d.pop("goal")

        participants = cast(list[str], d.pop("participants"))

        sink = RapidChatSinkIn.from_dict(d.pop("sink"))

        title = d.pop("title")

        _areas = d.pop("areas", UNSET)
        areas: list[RapidChatAreaIn | str] | Unset = UNSET
        if _areas is not UNSET:
            areas = []
            for areas_item_data in _areas:

                def _parse_areas_item(data: object) -> RapidChatAreaIn | str:
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        areas_item_type_1 = RapidChatAreaIn.from_dict(data)

                        return areas_item_type_1
                    except (TypeError, ValueError, AttributeError, KeyError):
                        pass
                    return cast(RapidChatAreaIn | str, data)

                areas_item = _parse_areas_item(areas_item_data)

                areas.append(areas_item)

        duration_minutes = d.pop("duration_minutes", UNSET)

        _limits = d.pop("limits", UNSET)
        limits: RapidChatLimitsIn | Unset
        if isinstance(_limits, Unset):
            limits = UNSET
        else:
            limits = RapidChatLimitsIn.from_dict(_limits)

        max_turns = d.pop("max_turns", UNSET)

        turn_timeout_seconds = d.pop("turn_timeout_seconds", UNSET)

        rapid_chat_in = cls(
            goal=goal,
            participants=participants,
            sink=sink,
            title=title,
            areas=areas,
            duration_minutes=duration_minutes,
            limits=limits,
            max_turns=max_turns,
            turn_timeout_seconds=turn_timeout_seconds,
        )

        return rapid_chat_in
