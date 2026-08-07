from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="ToolMetrics")


@_attrs_define
class ToolMetrics:
    """
    Attributes:
        avg_latency_ms (float):
        calls (int):
        denials (int):
        errors (int):
        tool (str):
    """

    avg_latency_ms: float
    calls: int
    denials: int
    errors: int
    tool: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        avg_latency_ms = self.avg_latency_ms

        calls = self.calls

        denials = self.denials

        errors = self.errors

        tool = self.tool

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "avg_latency_ms": avg_latency_ms,
                "calls": calls,
                "denials": denials,
                "errors": errors,
                "tool": tool,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        avg_latency_ms = d.pop("avg_latency_ms")

        calls = d.pop("calls")

        denials = d.pop("denials")

        errors = d.pop("errors")

        tool = d.pop("tool")

        tool_metrics = cls(
            avg_latency_ms=avg_latency_ms,
            calls=calls,
            denials=denials,
            errors=errors,
            tool=tool,
        )

        tool_metrics.additional_properties = d
        return tool_metrics

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
