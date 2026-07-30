from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.metrics_overview_by_state import MetricsOverviewByState


T = TypeVar("T", bound="MetricsOverview")


@_attrs_define
class MetricsOverview:
    """
    Attributes:
        active (int):
        avg_duration_seconds (float | None):
        by_state (MetricsOverviewByState):
        dlq (int):
        last_run_at (None | str):
        max_duration_seconds (float | None):
        runs_24h (int):
        runs_7d (int):
        succeeded (int):
        success_rate (float | None):
        tokens_in (int):
        tokens_out (int):
        tool_calls (int):
        total (int):
        window (int):
    """

    active: int
    avg_duration_seconds: float | None
    by_state: MetricsOverviewByState
    dlq: int
    last_run_at: None | str
    max_duration_seconds: float | None
    runs_24h: int
    runs_7d: int
    succeeded: int
    success_rate: float | None
    tokens_in: int
    tokens_out: int
    tool_calls: int
    total: int
    window: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        active = self.active

        avg_duration_seconds: float | None
        avg_duration_seconds = self.avg_duration_seconds

        by_state = self.by_state.to_dict()

        dlq = self.dlq

        last_run_at: None | str
        last_run_at = self.last_run_at

        max_duration_seconds: float | None
        max_duration_seconds = self.max_duration_seconds

        runs_24h = self.runs_24h

        runs_7d = self.runs_7d

        succeeded = self.succeeded

        success_rate: float | None
        success_rate = self.success_rate

        tokens_in = self.tokens_in

        tokens_out = self.tokens_out

        tool_calls = self.tool_calls

        total = self.total

        window = self.window

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "active": active,
                "avg_duration_seconds": avg_duration_seconds,
                "by_state": by_state,
                "dlq": dlq,
                "last_run_at": last_run_at,
                "max_duration_seconds": max_duration_seconds,
                "runs_24h": runs_24h,
                "runs_7d": runs_7d,
                "succeeded": succeeded,
                "success_rate": success_rate,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "tool_calls": tool_calls,
                "total": total,
                "window": window,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.metrics_overview_by_state import MetricsOverviewByState

        d = dict(src_dict)
        active = d.pop("active")

        def _parse_avg_duration_seconds(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        avg_duration_seconds = _parse_avg_duration_seconds(
            d.pop("avg_duration_seconds")
        )

        by_state = MetricsOverviewByState.from_dict(d.pop("by_state"))

        dlq = d.pop("dlq")

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

        runs_24h = d.pop("runs_24h")

        runs_7d = d.pop("runs_7d")

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

        window = d.pop("window")

        metrics_overview = cls(
            active=active,
            avg_duration_seconds=avg_duration_seconds,
            by_state=by_state,
            dlq=dlq,
            last_run_at=last_run_at,
            max_duration_seconds=max_duration_seconds,
            runs_24h=runs_24h,
            runs_7d=runs_7d,
            succeeded=succeeded,
            success_rate=success_rate,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tool_calls=tool_calls,
            total=total,
            window=window,
        )

        metrics_overview.additional_properties = d
        return metrics_overview

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
