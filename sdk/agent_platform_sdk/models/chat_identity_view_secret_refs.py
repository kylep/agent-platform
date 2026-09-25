from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.chat_identity_view_secret_refs_additional_property import (
        ChatIdentityViewSecretRefsAdditionalProperty,
    )


T = TypeVar("T", bound="ChatIdentityViewSecretRefs")


@_attrs_define
class ChatIdentityViewSecretRefs:
    """ """

    additional_properties: dict[str, ChatIdentityViewSecretRefsAdditionalProperty] = (
        _attrs_field(init=False, factory=dict)
    )

    def to_dict(self) -> dict[str, Any]:

        field_dict: dict[str, Any] = {}
        for prop_name, prop in self.additional_properties.items():
            field_dict[prop_name] = prop.to_dict()

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.chat_identity_view_secret_refs_additional_property import (
            ChatIdentityViewSecretRefsAdditionalProperty,
        )

        d = dict(src_dict)
        chat_identity_view_secret_refs = cls()

        additional_properties = {}
        for prop_name, prop_dict in d.items():
            additional_property = (
                ChatIdentityViewSecretRefsAdditionalProperty.from_dict(prop_dict)
            )

            additional_properties[prop_name] = additional_property

        chat_identity_view_secret_refs.additional_properties = additional_properties
        return chat_identity_view_secret_refs

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> ChatIdentityViewSecretRefsAdditionalProperty:
        return self.additional_properties[key]

    def __setitem__(
        self, key: str, value: ChatIdentityViewSecretRefsAdditionalProperty
    ) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
