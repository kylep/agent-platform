from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.secret_key_field import SecretKeyField


T = TypeVar("T", bound="SecretStatus")


@_attrs_define
class SecretStatus:
    """
    Attributes:
        declared (bool):
        hint (str):
        key (str):
        name (str):
        probeable (bool):
        required (bool):
        status (str):
        keys (list[SecretKeyField] | Unset):
    """

    declared: bool
    hint: str
    key: str
    name: str
    probeable: bool
    required: bool
    status: str
    keys: list[SecretKeyField] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        declared = self.declared

        hint = self.hint

        key = self.key

        name = self.name

        probeable = self.probeable

        required = self.required

        status = self.status

        keys: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.keys, Unset):
            keys = []
            for keys_item_data in self.keys:
                keys_item = keys_item_data.to_dict()
                keys.append(keys_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "declared": declared,
                "hint": hint,
                "key": key,
                "name": name,
                "probeable": probeable,
                "required": required,
                "status": status,
            }
        )
        if keys is not UNSET:
            field_dict["keys"] = keys

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.secret_key_field import SecretKeyField

        d = dict(src_dict)
        declared = d.pop("declared")

        hint = d.pop("hint")

        key = d.pop("key")

        name = d.pop("name")

        probeable = d.pop("probeable")

        required = d.pop("required")

        status = d.pop("status")

        _keys = d.pop("keys", UNSET)
        keys: list[SecretKeyField] | Unset = UNSET
        if _keys is not UNSET:
            keys = []
            for keys_item_data in _keys:
                keys_item = SecretKeyField.from_dict(keys_item_data)

                keys.append(keys_item)

        secret_status = cls(
            declared=declared,
            hint=hint,
            key=key,
            name=name,
            probeable=probeable,
            required=required,
            status=status,
            keys=keys,
        )

        secret_status.additional_properties = d
        return secret_status

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
