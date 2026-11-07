from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.guard_in_level_type_0 import GuardInLevelType0
from ..types import UNSET, Unset

T = TypeVar("T", bound="GuardIn")


@_attrs_define
class GuardIn:
    """
    Attributes:
        level (GuardInLevelType0 | None):
        note (str | Unset):  Default: ''.
    """

    level: GuardInLevelType0 | None
    note: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        level: None | str
        if isinstance(self.level, GuardInLevelType0):
            level = self.level.value
        else:
            level = self.level

        note = self.note

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "level": level,
            }
        )
        if note is not UNSET:
            field_dict["note"] = note

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_level(data: object) -> GuardInLevelType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                level_type_0 = GuardInLevelType0(data)

                return level_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(GuardInLevelType0 | None, data)

        level = _parse_level(d.pop("level"))

        note = d.pop("note", UNSET)

        guard_in = cls(
            level=level,
            note=note,
        )

        guard_in.additional_properties = d
        return guard_in

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
