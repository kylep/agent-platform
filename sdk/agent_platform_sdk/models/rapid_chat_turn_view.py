from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.rapid_chat_turn_view_parsed_type_0 import RapidChatTurnViewParsedType0


T = TypeVar("T", bound="RapidChatTurnView")


@_attrs_define
class RapidChatTurnView:
    """
    Attributes:
        agent (str):
        chat_id (str):
        id (str):
        pass_no (int):
        prompt (str):
        role (str):
        seq (int):
        started_at (str):
        state (str):
        area_key (None | str | Unset):
        finished_at (None | str | Unset):
        outcome (None | str | Unset):
        parsed (None | RapidChatTurnViewParsedType0 | Unset):
        reason (None | str | Unset):
        result (None | str | Unset):
        run_id (None | str | Unset):
    """

    agent: str
    chat_id: str
    id: str
    pass_no: int
    prompt: str
    role: str
    seq: int
    started_at: str
    state: str
    area_key: None | str | Unset = UNSET
    finished_at: None | str | Unset = UNSET
    outcome: None | str | Unset = UNSET
    parsed: None | RapidChatTurnViewParsedType0 | Unset = UNSET
    reason: None | str | Unset = UNSET
    result: None | str | Unset = UNSET
    run_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.rapid_chat_turn_view_parsed_type_0 import (
            RapidChatTurnViewParsedType0,
        )

        agent = self.agent

        chat_id = self.chat_id

        id = self.id

        pass_no = self.pass_no

        prompt = self.prompt

        role = self.role

        seq = self.seq

        started_at = self.started_at

        state = self.state

        area_key: None | str | Unset
        if isinstance(self.area_key, Unset):
            area_key = UNSET
        else:
            area_key = self.area_key

        finished_at: None | str | Unset
        if isinstance(self.finished_at, Unset):
            finished_at = UNSET
        else:
            finished_at = self.finished_at

        outcome: None | str | Unset
        if isinstance(self.outcome, Unset):
            outcome = UNSET
        else:
            outcome = self.outcome

        parsed: dict[str, Any] | None | Unset
        if isinstance(self.parsed, Unset):
            parsed = UNSET
        elif isinstance(self.parsed, RapidChatTurnViewParsedType0):
            parsed = self.parsed.to_dict()
        else:
            parsed = self.parsed

        reason: None | str | Unset
        if isinstance(self.reason, Unset):
            reason = UNSET
        else:
            reason = self.reason

        result: None | str | Unset
        if isinstance(self.result, Unset):
            result = UNSET
        else:
            result = self.result

        run_id: None | str | Unset
        if isinstance(self.run_id, Unset):
            run_id = UNSET
        else:
            run_id = self.run_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "chat_id": chat_id,
                "id": id,
                "pass_no": pass_no,
                "prompt": prompt,
                "role": role,
                "seq": seq,
                "started_at": started_at,
                "state": state,
            }
        )
        if area_key is not UNSET:
            field_dict["area_key"] = area_key
        if finished_at is not UNSET:
            field_dict["finished_at"] = finished_at
        if outcome is not UNSET:
            field_dict["outcome"] = outcome
        if parsed is not UNSET:
            field_dict["parsed"] = parsed
        if reason is not UNSET:
            field_dict["reason"] = reason
        if result is not UNSET:
            field_dict["result"] = result
        if run_id is not UNSET:
            field_dict["run_id"] = run_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.rapid_chat_turn_view_parsed_type_0 import (
            RapidChatTurnViewParsedType0,
        )

        d = dict(src_dict)
        agent = d.pop("agent")

        chat_id = d.pop("chat_id")

        id = d.pop("id")

        pass_no = d.pop("pass_no")

        prompt = d.pop("prompt")

        role = d.pop("role")

        seq = d.pop("seq")

        started_at = d.pop("started_at")

        state = d.pop("state")

        def _parse_area_key(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        area_key = _parse_area_key(d.pop("area_key", UNSET))

        def _parse_finished_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        finished_at = _parse_finished_at(d.pop("finished_at", UNSET))

        def _parse_outcome(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        outcome = _parse_outcome(d.pop("outcome", UNSET))

        def _parse_parsed(data: object) -> None | RapidChatTurnViewParsedType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                parsed_type_0 = RapidChatTurnViewParsedType0.from_dict(data)

                return parsed_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | RapidChatTurnViewParsedType0 | Unset, data)

        parsed = _parse_parsed(d.pop("parsed", UNSET))

        def _parse_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        reason = _parse_reason(d.pop("reason", UNSET))

        def _parse_result(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        result = _parse_result(d.pop("result", UNSET))

        def _parse_run_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        run_id = _parse_run_id(d.pop("run_id", UNSET))

        rapid_chat_turn_view = cls(
            agent=agent,
            chat_id=chat_id,
            id=id,
            pass_no=pass_no,
            prompt=prompt,
            role=role,
            seq=seq,
            started_at=started_at,
            state=state,
            area_key=area_key,
            finished_at=finished_at,
            outcome=outcome,
            parsed=parsed,
            reason=reason,
            result=result,
            run_id=run_id,
        )

        rapid_chat_turn_view.additional_properties = d
        return rapid_chat_turn_view

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
