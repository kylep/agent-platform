from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.def_ref_kind import DefRefKind

T = TypeVar("T", bound="DefRef")


@_attrs_define
class DefRef:
    """
    Attributes:
        kind (DefRefKind):
        name (str):
    """

    kind: DefRefKind
    name: str

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value

        name = self.name

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "kind": kind,
                "name": name,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = DefRefKind(d.pop("kind"))

        name = d.pop("name")

        def_ref = cls(
            kind=kind,
            name=name,
        )

        return def_ref
