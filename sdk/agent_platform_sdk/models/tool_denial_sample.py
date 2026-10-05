from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="ToolDenialSample")


@_attrs_define
class ToolDenialSample:
    """
    Attributes:
        action (None | str):
        agent (str):
        decision (str):
        run_id (None | str):
        ts (None | str):
    """

    action: None | str
    agent: str
    decision: str
    run_id: None | str
    ts: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        action: None | str
        action = self.action

        agent = self.agent

        decision = self.decision

        run_id: None | str
        run_id = self.run_id

        ts: None | str
        ts = self.ts

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "action": action,
                "agent": agent,
                "decision": decision,
                "run_id": run_id,
                "ts": ts,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_action(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        action = _parse_action(d.pop("action"))

        agent = d.pop("agent")

        decision = d.pop("decision")

        def _parse_run_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        run_id = _parse_run_id(d.pop("run_id"))

        def _parse_ts(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        ts = _parse_ts(d.pop("ts"))

        tool_denial_sample = cls(
            action=action,
            agent=agent,
            decision=decision,
            run_id=run_id,
            ts=ts,
        )

        tool_denial_sample.additional_properties = d
        return tool_denial_sample

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
