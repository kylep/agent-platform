from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.quota_in_limits import QuotaInLimits


T = TypeVar("T", bound="QuotaIn")


@_attrs_define
class QuotaIn:
    """
    Attributes:
        limits (QuotaInLimits):
    """

    limits: QuotaInLimits

    def to_dict(self) -> dict[str, Any]:
        limits = self.limits.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "limits": limits,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.quota_in_limits import QuotaInLimits

        d = dict(src_dict)
        limits = QuotaInLimits.from_dict(d.pop("limits"))

        quota_in = cls(
            limits=limits,
        )

        return quota_in
