from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.chat_identity_view_secret_refs import ChatIdentityViewSecretRefs


T = TypeVar("T", bound="ChatIdentityView")


@_attrs_define
class ChatIdentityView:
    """
    Attributes:
        bound_routes (int):
        configured (bool):
        connector (str):
        display_name (str):
        id (str):
        secret_refs (ChatIdentityViewSecretRefs):
        status (str):
    """

    bound_routes: int
    configured: bool
    connector: str
    display_name: str
    id: str
    secret_refs: ChatIdentityViewSecretRefs
    status: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        bound_routes = self.bound_routes

        configured = self.configured

        connector = self.connector

        display_name = self.display_name

        id = self.id

        secret_refs = self.secret_refs.to_dict()

        status = self.status

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "bound_routes": bound_routes,
                "configured": configured,
                "connector": connector,
                "display_name": display_name,
                "id": id,
                "secret_refs": secret_refs,
                "status": status,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.chat_identity_view_secret_refs import ChatIdentityViewSecretRefs

        d = dict(src_dict)
        bound_routes = d.pop("bound_routes")

        configured = d.pop("configured")

        connector = d.pop("connector")

        display_name = d.pop("display_name")

        id = d.pop("id")

        secret_refs = ChatIdentityViewSecretRefs.from_dict(d.pop("secret_refs"))

        status = d.pop("status")

        chat_identity_view = cls(
            bound_routes=bound_routes,
            configured=configured,
            connector=connector,
            display_name=display_name,
            id=id,
            secret_refs=secret_refs,
            status=status,
        )

        chat_identity_view.additional_properties = d
        return chat_identity_view

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
