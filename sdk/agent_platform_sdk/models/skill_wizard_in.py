from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="SkillWizardIn")


@_attrs_define
class SkillWizardIn:
    """
    Attributes:
        name (str):
        purpose (str):
        notes (str | Unset):  Default: ''.
        when_to_use (str | Unset):  Default: ''.
    """

    name: str
    purpose: str
    notes: str | Unset = ""
    when_to_use: str | Unset = ""

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        purpose = self.purpose

        notes = self.notes

        when_to_use = self.when_to_use

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "name": name,
                "purpose": purpose,
            }
        )
        if notes is not UNSET:
            field_dict["notes"] = notes
        if when_to_use is not UNSET:
            field_dict["when_to_use"] = when_to_use

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        name = d.pop("name")

        purpose = d.pop("purpose")

        notes = d.pop("notes", UNSET)

        when_to_use = d.pop("when_to_use", UNSET)

        skill_wizard_in = cls(
            name=name,
            purpose=purpose,
            notes=notes,
            when_to_use=when_to_use,
        )

        return skill_wizard_in
