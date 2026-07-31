from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.skill_wizard_secret import SkillWizardSecret


T = TypeVar("T", bound="SkillWizardIn")


@_attrs_define
class SkillWizardIn:
    """
    Attributes:
        name (str):
        purpose (str):
        notes (str | Unset):  Default: ''.
        secret (None | SkillWizardSecret | Unset):
        when_to_use (str | Unset):  Default: ''.
    """

    name: str
    purpose: str
    notes: str | Unset = ""
    secret: None | SkillWizardSecret | Unset = UNSET
    when_to_use: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.skill_wizard_secret import SkillWizardSecret

        name = self.name

        purpose = self.purpose

        notes = self.notes

        secret: dict[str, Any] | None | Unset
        if isinstance(self.secret, Unset):
            secret = UNSET
        elif isinstance(self.secret, SkillWizardSecret):
            secret = self.secret.to_dict()
        else:
            secret = self.secret

        when_to_use = self.when_to_use

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "purpose": purpose,
            }
        )
        if notes is not UNSET:
            field_dict["notes"] = notes
        if secret is not UNSET:
            field_dict["secret"] = secret
        if when_to_use is not UNSET:
            field_dict["when_to_use"] = when_to_use

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.skill_wizard_secret import SkillWizardSecret

        d = dict(src_dict)
        name = d.pop("name")

        purpose = d.pop("purpose")

        notes = d.pop("notes", UNSET)

        def _parse_secret(data: object) -> None | SkillWizardSecret | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                secret_type_0 = SkillWizardSecret.from_dict(data)

                return secret_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | SkillWizardSecret | Unset, data)

        secret = _parse_secret(d.pop("secret", UNSET))

        when_to_use = d.pop("when_to_use", UNSET)

        skill_wizard_in = cls(
            name=name,
            purpose=purpose,
            notes=notes,
            secret=secret,
            when_to_use=when_to_use,
        )

        skill_wizard_in.additional_properties = d
        return skill_wizard_in

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
