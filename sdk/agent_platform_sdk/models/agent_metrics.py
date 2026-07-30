from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.agent_metrics_by_state import AgentMetricsByState


T = TypeVar("T", bound="AgentMetrics")


@_attrs_define
class AgentMetrics:
    """
    Attributes:
        active (int):
        agent (str):
        avg_duration_seconds (float | None):
        by_state (AgentMetricsByState):
        failure_streak (int):
        last_run_at (None | str):
        max_duration_seconds (float | None):
        succeeded (int):
        success_rate (float | None):
        tokens_in (int):
        tokens_out (int):
        tool_calls (int):
        total (int):
    """

    active: int
    agent: str
    avg_duration_seconds: float | None
    by_state: AgentMetricsByState
    failure_streak: int
    last_run_at: None | str
    max_duration_seconds: float | None
    succeeded: int
    success_rate: float | None
    tokens_in: int
    tokens_out: int
    tool_calls: int
    total: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        active = self.active

        agent = self.agent

        avg_duration_seconds: float | None
        avg_duration_seconds = self.avg_duration_seconds

        by_state = self.by_state.to_dict()

        failure_streak = self.failure_streak

        last_run_at: None | str
        last_run_at = self.last_run_at

        max_duration_seconds: float | None
        max_duration_seconds = self.max_duration_seconds

        succeeded = self.succeeded

        success_rate: float | None
        success_rate = self.success_rate

        tokens_in = self.tokens_in

        tokens_out = self.tokens_out

        tool_calls = self.tool_calls

        total = self.total

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "active": active,
                "agent": agent,
                "avg_duration_seconds": avg_duration_seconds,
                "by_state": by_state,
                "failure_streak": failure_streak,
                "last_run_at": last_run_at,
                "max_duration_seconds": max_duration_seconds,
                "succeeded": succeeded,
                "success_rate": success_rate,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "tool_calls": tool_calls,
                "total": total,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_metrics_by_state import AgentMetricsByState

        d = dict(src_dict)
        active = d.pop("active")

        agent = d.pop("agent")

        def _parse_avg_duration_seconds(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        avg_duration_seconds = _parse_avg_duration_seconds(
            d.pop("avg_duration_seconds")
        )

        by_state = AgentMetricsByState.from_dict(d.pop("by_state"))

        failure_streak = d.pop("failure_streak")

        def _parse_last_run_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        last_run_at = _parse_last_run_at(d.pop("last_run_at"))

        def _parse_max_duration_seconds(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        max_duration_seconds = _parse_max_duration_seconds(
            d.pop("max_duration_seconds")
        )

        succeeded = d.pop("succeeded")

        def _parse_success_rate(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        success_rate = _parse_success_rate(d.pop("success_rate"))

        tokens_in = d.pop("tokens_in")

        tokens_out = d.pop("tokens_out")

        tool_calls = d.pop("tool_calls")

        total = d.pop("total")

        agent_metrics = cls(
            active=active,
            agent=agent,
            avg_duration_seconds=avg_duration_seconds,
            by_state=by_state,
            failure_streak=failure_streak,
            last_run_at=last_run_at,
            max_duration_seconds=max_duration_seconds,
            succeeded=succeeded,
            success_rate=success_rate,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tool_calls=tool_calls,
            total=total,
        )

        agent_metrics.additional_properties = d
        return agent_metrics

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
