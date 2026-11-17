from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="UiFontRef")


@_attrs_define
class UiFontRef:
    """
    Attributes:
        family (str):
        sha256 (str):
        weight (int):
    """

    family: str
    sha256: str
    weight: int

    def to_dict(self) -> dict[str, Any]:
        family = self.family

        sha256 = self.sha256

        weight = self.weight

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "family": family,
                "sha256": sha256,
                "weight": weight,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        family = d.pop("family")

        sha256 = d.pop("sha256")

        weight = d.pop("weight")

        ui_font_ref = cls(
            family=family,
            sha256=sha256,
            weight=weight,
        )

        return ui_font_ref
