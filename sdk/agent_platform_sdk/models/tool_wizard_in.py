from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.tool_wizard_secret import ToolWizardSecret


T = TypeVar("T", bound="ToolWizardIn")


@_attrs_define
class ToolWizardIn:
    """
    Attributes:
        name (str):
        purpose (str):
        arguments (str | Unset):  Default: ''.
        needs_database (bool | Unset):  Default: False.
        notes (str | Unset):  Default: ''.
        secret (None | ToolWizardSecret | Unset):
    """

    name: str
    purpose: str
    arguments: str | Unset = ""
    needs_database: bool | Unset = False
    notes: str | Unset = ""
    secret: None | ToolWizardSecret | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.tool_wizard_secret import ToolWizardSecret

        name = self.name

        purpose = self.purpose

        arguments = self.arguments

        needs_database = self.needs_database

        notes = self.notes

        secret: dict[str, Any] | None | Unset
        if isinstance(self.secret, Unset):
            secret = UNSET
        elif isinstance(self.secret, ToolWizardSecret):
            secret = self.secret.to_dict()
        else:
            secret = self.secret

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "purpose": purpose,
            }
        )
        if arguments is not UNSET:
            field_dict["arguments"] = arguments
        if needs_database is not UNSET:
            field_dict["needs_database"] = needs_database
        if notes is not UNSET:
            field_dict["notes"] = notes
        if secret is not UNSET:
            field_dict["secret"] = secret

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.tool_wizard_secret import ToolWizardSecret

        d = dict(src_dict)
        name = d.pop("name")

        purpose = d.pop("purpose")

        arguments = d.pop("arguments", UNSET)

        needs_database = d.pop("needs_database", UNSET)

        notes = d.pop("notes", UNSET)

        def _parse_secret(data: object) -> None | ToolWizardSecret | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                secret_type_0 = ToolWizardSecret.from_dict(data)

                return secret_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | ToolWizardSecret | Unset, data)

        secret = _parse_secret(d.pop("secret", UNSET))

        tool_wizard_in = cls(
            name=name,
            purpose=purpose,
            arguments=arguments,
            needs_database=needs_database,
            notes=notes,
            secret=secret,
        )

        tool_wizard_in.additional_properties = d
        return tool_wizard_in

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
