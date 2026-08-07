from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="ToolAuditView")


@_attrs_define
class ToolAuditView:
    """
    Attributes:
        agent (str):
        args_digest (str):
        decision (str):
        id (str):
        initiated_by (None | str):
        latency_ms (int):
        result_bytes (int):
        run_id (None | str):
        tool (str):
        ts (None | str):
    """

    agent: str
    args_digest: str
    decision: str
    id: str
    initiated_by: None | str
    latency_ms: int
    result_bytes: int
    run_id: None | str
    tool: str
    ts: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent

        args_digest = self.args_digest

        decision = self.decision

        id = self.id

        initiated_by: None | str
        initiated_by = self.initiated_by

        latency_ms = self.latency_ms

        result_bytes = self.result_bytes

        run_id: None | str
        run_id = self.run_id

        tool = self.tool

        ts: None | str
        ts = self.ts

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "args_digest": args_digest,
                "decision": decision,
                "id": id,
                "initiated_by": initiated_by,
                "latency_ms": latency_ms,
                "result_bytes": result_bytes,
                "run_id": run_id,
                "tool": tool,
                "ts": ts,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        agent = d.pop("agent")

        args_digest = d.pop("args_digest")

        decision = d.pop("decision")

        id = d.pop("id")

        def _parse_initiated_by(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        initiated_by = _parse_initiated_by(d.pop("initiated_by"))

        latency_ms = d.pop("latency_ms")

        result_bytes = d.pop("result_bytes")

        def _parse_run_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        run_id = _parse_run_id(d.pop("run_id"))

        tool = d.pop("tool")

        def _parse_ts(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        ts = _parse_ts(d.pop("ts"))

        tool_audit_view = cls(
            agent=agent,
            args_digest=args_digest,
            decision=decision,
            id=id,
            initiated_by=initiated_by,
            latency_ms=latency_ms,
            result_bytes=result_bytes,
            run_id=run_id,
            tool=tool,
            ts=ts,
        )

        tool_audit_view.additional_properties = d
        return tool_audit_view

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
