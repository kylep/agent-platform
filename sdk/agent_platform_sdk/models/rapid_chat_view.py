from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.rapid_chat_view_areas_item import RapidChatViewAreasItem
    from ..models.rapid_chat_view_last_quota_type_0 import RapidChatViewLastQuotaType0
    from ..models.rapid_chat_view_limits import RapidChatViewLimits
    from ..models.rapid_chat_view_output_ref_type_0 import RapidChatViewOutputRefType0
    from ..models.rapid_chat_view_sink import RapidChatViewSink
    from ..models.rapid_chat_view_turn_counts import RapidChatViewTurnCounts


T = TypeVar("T", bound="RapidChatView")


@_attrs_define
class RapidChatView:
    """
    Attributes:
        areas (list[RapidChatViewAreasItem]):
        created_at (str):
        created_by (str):
        duration_minutes (int):
        goal (str):
        id (str):
        integrated_pass (int):
        limits (RapidChatViewLimits):
        max_turns (int):
        participants (list[str]):
        sink (RapidChatViewSink):
        state (str):
        title (str):
        turn_timeout_seconds (int):
        turns_total (int):
        end_reason (None | str | Unset):
        ended_at (None | str | Unset):
        initiated_by (None | str | Unset):
        last_error (None | str | Unset):
        last_quota (None | RapidChatViewLastQuotaType0 | Unset):
        output_ref (None | RapidChatViewOutputRefType0 | Unset):
        started_at (None | str | Unset):
        summary (None | str | Unset):
        turn_counts (RapidChatViewTurnCounts | Unset):
    """

    areas: list[RapidChatViewAreasItem]
    created_at: str
    created_by: str
    duration_minutes: int
    goal: str
    id: str
    integrated_pass: int
    limits: RapidChatViewLimits
    max_turns: int
    participants: list[str]
    sink: RapidChatViewSink
    state: str
    title: str
    turn_timeout_seconds: int
    turns_total: int
    end_reason: None | str | Unset = UNSET
    ended_at: None | str | Unset = UNSET
    initiated_by: None | str | Unset = UNSET
    last_error: None | str | Unset = UNSET
    last_quota: None | RapidChatViewLastQuotaType0 | Unset = UNSET
    output_ref: None | RapidChatViewOutputRefType0 | Unset = UNSET
    started_at: None | str | Unset = UNSET
    summary: None | str | Unset = UNSET
    turn_counts: RapidChatViewTurnCounts | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.rapid_chat_view_last_quota_type_0 import (
            RapidChatViewLastQuotaType0,
        )
        from ..models.rapid_chat_view_output_ref_type_0 import (
            RapidChatViewOutputRefType0,
        )

        areas = []
        for areas_item_data in self.areas:
            areas_item = areas_item_data.to_dict()
            areas.append(areas_item)

        created_at = self.created_at

        created_by = self.created_by

        duration_minutes = self.duration_minutes

        goal = self.goal

        id = self.id

        integrated_pass = self.integrated_pass

        limits = self.limits.to_dict()

        max_turns = self.max_turns

        participants = self.participants

        sink = self.sink.to_dict()

        state = self.state

        title = self.title

        turn_timeout_seconds = self.turn_timeout_seconds

        turns_total = self.turns_total

        end_reason: None | str | Unset
        if isinstance(self.end_reason, Unset):
            end_reason = UNSET
        else:
            end_reason = self.end_reason

        ended_at: None | str | Unset
        if isinstance(self.ended_at, Unset):
            ended_at = UNSET
        else:
            ended_at = self.ended_at

        initiated_by: None | str | Unset
        if isinstance(self.initiated_by, Unset):
            initiated_by = UNSET
        else:
            initiated_by = self.initiated_by

        last_error: None | str | Unset
        if isinstance(self.last_error, Unset):
            last_error = UNSET
        else:
            last_error = self.last_error

        last_quota: dict[str, Any] | None | Unset
        if isinstance(self.last_quota, Unset):
            last_quota = UNSET
        elif isinstance(self.last_quota, RapidChatViewLastQuotaType0):
            last_quota = self.last_quota.to_dict()
        else:
            last_quota = self.last_quota

        output_ref: dict[str, Any] | None | Unset
        if isinstance(self.output_ref, Unset):
            output_ref = UNSET
        elif isinstance(self.output_ref, RapidChatViewOutputRefType0):
            output_ref = self.output_ref.to_dict()
        else:
            output_ref = self.output_ref

        started_at: None | str | Unset
        if isinstance(self.started_at, Unset):
            started_at = UNSET
        else:
            started_at = self.started_at

        summary: None | str | Unset
        if isinstance(self.summary, Unset):
            summary = UNSET
        else:
            summary = self.summary

        turn_counts: dict[str, Any] | Unset = UNSET
        if not isinstance(self.turn_counts, Unset):
            turn_counts = self.turn_counts.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "areas": areas,
                "created_at": created_at,
                "created_by": created_by,
                "duration_minutes": duration_minutes,
                "goal": goal,
                "id": id,
                "integrated_pass": integrated_pass,
                "limits": limits,
                "max_turns": max_turns,
                "participants": participants,
                "sink": sink,
                "state": state,
                "title": title,
                "turn_timeout_seconds": turn_timeout_seconds,
                "turns_total": turns_total,
            }
        )
        if end_reason is not UNSET:
            field_dict["end_reason"] = end_reason
        if ended_at is not UNSET:
            field_dict["ended_at"] = ended_at
        if initiated_by is not UNSET:
            field_dict["initiated_by"] = initiated_by
        if last_error is not UNSET:
            field_dict["last_error"] = last_error
        if last_quota is not UNSET:
            field_dict["last_quota"] = last_quota
        if output_ref is not UNSET:
            field_dict["output_ref"] = output_ref
        if started_at is not UNSET:
            field_dict["started_at"] = started_at
        if summary is not UNSET:
            field_dict["summary"] = summary
        if turn_counts is not UNSET:
            field_dict["turn_counts"] = turn_counts

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.rapid_chat_view_areas_item import RapidChatViewAreasItem
        from ..models.rapid_chat_view_last_quota_type_0 import (
            RapidChatViewLastQuotaType0,
        )
        from ..models.rapid_chat_view_limits import RapidChatViewLimits
        from ..models.rapid_chat_view_output_ref_type_0 import (
            RapidChatViewOutputRefType0,
        )
        from ..models.rapid_chat_view_sink import RapidChatViewSink
        from ..models.rapid_chat_view_turn_counts import RapidChatViewTurnCounts

        d = dict(src_dict)
        areas = []
        _areas = d.pop("areas")
        for areas_item_data in _areas:
            areas_item = RapidChatViewAreasItem.from_dict(areas_item_data)

            areas.append(areas_item)

        created_at = d.pop("created_at")

        created_by = d.pop("created_by")

        duration_minutes = d.pop("duration_minutes")

        goal = d.pop("goal")

        id = d.pop("id")

        integrated_pass = d.pop("integrated_pass")

        limits = RapidChatViewLimits.from_dict(d.pop("limits"))

        max_turns = d.pop("max_turns")

        participants = cast(list[str], d.pop("participants"))

        sink = RapidChatViewSink.from_dict(d.pop("sink"))

        state = d.pop("state")

        title = d.pop("title")

        turn_timeout_seconds = d.pop("turn_timeout_seconds")

        turns_total = d.pop("turns_total")

        def _parse_end_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        end_reason = _parse_end_reason(d.pop("end_reason", UNSET))

        def _parse_ended_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        ended_at = _parse_ended_at(d.pop("ended_at", UNSET))

        def _parse_initiated_by(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        initiated_by = _parse_initiated_by(d.pop("initiated_by", UNSET))

        def _parse_last_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        last_error = _parse_last_error(d.pop("last_error", UNSET))

        def _parse_last_quota(
            data: object,
        ) -> None | RapidChatViewLastQuotaType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                last_quota_type_0 = RapidChatViewLastQuotaType0.from_dict(data)

                return last_quota_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | RapidChatViewLastQuotaType0 | Unset, data)

        last_quota = _parse_last_quota(d.pop("last_quota", UNSET))

        def _parse_output_ref(
            data: object,
        ) -> None | RapidChatViewOutputRefType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                output_ref_type_0 = RapidChatViewOutputRefType0.from_dict(data)

                return output_ref_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | RapidChatViewOutputRefType0 | Unset, data)

        output_ref = _parse_output_ref(d.pop("output_ref", UNSET))

        def _parse_started_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        started_at = _parse_started_at(d.pop("started_at", UNSET))

        def _parse_summary(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        summary = _parse_summary(d.pop("summary", UNSET))

        _turn_counts = d.pop("turn_counts", UNSET)
        turn_counts: RapidChatViewTurnCounts | Unset
        if isinstance(_turn_counts, Unset):
            turn_counts = UNSET
        else:
            turn_counts = RapidChatViewTurnCounts.from_dict(_turn_counts)

        rapid_chat_view = cls(
            areas=areas,
            created_at=created_at,
            created_by=created_by,
            duration_minutes=duration_minutes,
            goal=goal,
            id=id,
            integrated_pass=integrated_pass,
            limits=limits,
            max_turns=max_turns,
            participants=participants,
            sink=sink,
            state=state,
            title=title,
            turn_timeout_seconds=turn_timeout_seconds,
            turns_total=turns_total,
            end_reason=end_reason,
            ended_at=ended_at,
            initiated_by=initiated_by,
            last_error=last_error,
            last_quota=last_quota,
            output_ref=output_ref,
            started_at=started_at,
            summary=summary,
            turn_counts=turn_counts,
        )

        rapid_chat_view.additional_properties = d
        return rapid_chat_view

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
