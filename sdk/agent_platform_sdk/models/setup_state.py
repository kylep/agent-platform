from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.secret_status import SecretStatus


T = TypeVar("T", bound="SetupState")


@_attrs_define
class SetupState:
    """
    Attributes:
        needs_admin (bool):
        secrets (list[SecretStatus]):
    """

    needs_admin: bool
    secrets: list[SecretStatus]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        needs_admin = self.needs_admin

        secrets = []
        for secrets_item_data in self.secrets:
            secrets_item = secrets_item_data.to_dict()
            secrets.append(secrets_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "needs_admin": needs_admin,
                "secrets": secrets,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.secret_status import SecretStatus

        d = dict(src_dict)
        needs_admin = d.pop("needs_admin")

        secrets = []
        _secrets = d.pop("secrets")
        for secrets_item_data in _secrets:
            secrets_item = SecretStatus.from_dict(secrets_item_data)

            secrets.append(secrets_item)

        setup_state = cls(
            needs_admin=needs_admin,
            secrets=secrets,
        )

        setup_state.additional_properties = d
        return setup_state

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
