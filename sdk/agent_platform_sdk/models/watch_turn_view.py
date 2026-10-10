from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="WatchTurnView")


@_attrs_define
class WatchTurnView:
    """
    Attributes:
        agent (str):
        delivery_id (None | str):
        finished_at (None | str):
        outcome (str):
        position (int):
        reason (str):
        run_id (None | str):
        started_at (None | str):
        state (str):
        pass_no (int | Unset):  Default: 0.
    """

    agent: str
    delivery_id: None | str
    finished_at: None | str
    outcome: str
    position: int
    reason: str
    run_id: None | str
    started_at: None | str
    state: str
    pass_no: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent

        delivery_id: None | str
        delivery_id = self.delivery_id

        finished_at: None | str
        finished_at = self.finished_at

        outcome = self.outcome

        position = self.position

        reason = self.reason

        run_id: None | str
        run_id = self.run_id

        started_at: None | str
        started_at = self.started_at

        state = self.state

        pass_no = self.pass_no

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "delivery_id": delivery_id,
                "finished_at": finished_at,
                "outcome": outcome,
                "position": position,
                "reason": reason,
                "run_id": run_id,
                "started_at": started_at,
                "state": state,
            }
        )
        if pass_no is not UNSET:
            field_dict["pass_no"] = pass_no

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        agent = d.pop("agent")

        def _parse_delivery_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        delivery_id = _parse_delivery_id(d.pop("delivery_id"))

        def _parse_finished_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        finished_at = _parse_finished_at(d.pop("finished_at"))

        outcome = d.pop("outcome")

        position = d.pop("position")

        reason = d.pop("reason")

        def _parse_run_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        run_id = _parse_run_id(d.pop("run_id"))

        def _parse_started_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        started_at = _parse_started_at(d.pop("started_at"))

        state = d.pop("state")

        pass_no = d.pop("pass_no", UNSET)

        watch_turn_view = cls(
            agent=agent,
            delivery_id=delivery_id,
            finished_at=finished_at,
            outcome=outcome,
            position=position,
            reason=reason,
            run_id=run_id,
            started_at=started_at,
            state=state,
            pass_no=pass_no,
        )

        watch_turn_view.additional_properties = d
        return watch_turn_view

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
