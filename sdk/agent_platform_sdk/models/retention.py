from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.retention_per_agent_days import RetentionPerAgentDays


T = TypeVar("T", bound="Retention")


@_attrs_define
class Retention:
    """
    Attributes:
        default_days (int):
        per_agent_days (RetentionPerAgentDays):
    """

    default_days: int
    per_agent_days: RetentionPerAgentDays
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        default_days = self.default_days

        per_agent_days = self.per_agent_days.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "default_days": default_days,
                "per_agent_days": per_agent_days,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.retention_per_agent_days import RetentionPerAgentDays

        d = dict(src_dict)
        default_days = d.pop("default_days")

        per_agent_days = RetentionPerAgentDays.from_dict(d.pop("per_agent_days"))

        retention = cls(
            default_days=default_days,
            per_agent_days=per_agent_days,
        )

        retention.additional_properties = d
        return retention

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
